"""Pipeline: source -> AST -> typed IR -> WAT -> native binary -> optional wasm-opt."""

from __future__ import annotations

import os
import json
import shlex
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .check import check_module
from .emit_wat import emit_module
from .errors import CompileError
from . import frontend as source_frontend
from . import selfhost
from .project import Project
from .wasm import EncodeError, encode

WASM_FEATURES = [
    "--enable-gc",
    "--enable-reference-types",
    "--enable-bulk-memory",
    "--enable-nontrapping-float-to-int",
    "--enable-sign-ext",
    "--enable-multivalue",
    "--enable-exception-handling",
]


class ToolError(Exception):
    pass


@dataclass
class Result:
    # Direct self-hosted binary builds do not produce text. Request keep_wat
    # (or call compile_to_wat) when the caller needs the emitted WAT as well.
    wat: str | None
    wasm_path: Path | None = None
    wit_path: Path | None = None
    js_path: Path | None = None
    dts_path: Path | None = None


def compile_to_wat(source: str, filename: str = "<input>", *, component: bool = False,
                   frontend: str | None = None, compiler: str | None = None,
                   module_root: Path | None = None, _project: Project | None = None,
                   exports: list[str] | None = None) -> str:
    if component and exports:
        raise ValueError("library and component output are mutually exclusive")
    project = _project
    if project is None and (module_root is not None or Path(filename).is_file()):
        project = Project(Path(filename), source, module_root)
    if selfhost.selected(compiler):
        if frontend not in (None, "snek"):
            raise ValueError("the Snek compiler owns its frontend; --frontend cpython requires --compiler python")
        if project:
            wat = project.compile(wat=True, component=component, **({"exports": exports} if exports else {})).decode("utf-8")
            if not wat.startswith("(module"):
                raise RuntimeError("Snek compiler did not return core WAT")
            return wat
        return selfhost.wat(source, component=component, **({"exports": exports} if exports else {}))
    selected = source_frontend.select(frontend)
    entry = project.entry if project else "library"
    if project:
        owned = project.owned_tree(**({"library": True} if exports else {}))
        tree = source_frontend.to_ast(owned)
        # A single source keeps its chosen reference frontend unchanged.
        if not exports and len(project.sources) == 1 and "." not in project.entry:
            tree = source_frontend.parse_source(source, filename, frontend=selected)
    elif exports:
        from bootstrap.modules import module_library
        tree = source_frontend.to_ast(module_library(source))
    else:
        tree = source_frontend.parse_source(source, filename, frontend=selected)
    try:
        return emit_module(check_module(tree, frontend=selected), component=component,
                           **({"exports": exports, "entry": entry} if exports else {}))
    except CompileError as error:
        if project:
            project.locate(error)
        raise


def find_wasm_opt() -> list[str]:
    if env := os.environ.get("SNEK_WASM_OPT"):
        return shlex.split(env)
    if path := shutil.which("wasm-opt"):
        return [path]
    raise ToolError(
        "wasm-opt (Binaryen) not found. Install it (`brew install binaryen`, `apt install binaryen`, or "
        "`npm i -g binaryen`), point SNEK_WASM_OPT at it, or use --no-opt.")


def assemble(wat_path: Path, wasm_path: Path, optimize: bool = True, *, compiler: str | None = None) -> None:
    try:
        binary = selfhost.encode(wat_path.read_text()) if selfhost.selected(compiler) else encode(wat_path.read_text())
    except EncodeError as e:
        raise ToolError(f"native Wasm encoding failed: {e}") from e
    write_binary(binary, wasm_path, optimize)


def write_binary(binary: bytes, wasm_path: Path, optimize: bool = True) -> None:
    """Write already-encoded Wasm, optionally running the shared optimizer."""
    if not optimize:
        wasm_path.write_bytes(binary)
        return
    with tempfile.TemporaryDirectory(prefix="snek-opt-") as d:
        raw = Path(d, "raw.wasm")
        raw.write_bytes(binary)
        # Nothing with a GC type crosses the module boundary, so Binaryen may
        # assume a closed world and refine/merge/devirtualize types freely.
        cmd = [*find_wasm_opt(), str(raw), "-o", str(wasm_path), *WASM_FEATURES,
               "-O3", "--closed-world"]
        proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise ToolError(f"wasm-opt failed:\n{proc.stderr.strip()}")


def find_wasm_tools() -> list[str]:
    if env := os.environ.get("SNEK_WASM_TOOLS"):
        return shlex.split(env)
    if path := shutil.which("wasm-tools"):
        return [path]
    raise ToolError("component output needs wasm-tools (1.250+); install it or set SNEK_WASM_TOOLS")


def wasm_tools(*args: str) -> str:
    proc = subprocess.run([*find_wasm_tools(), *args], capture_output=True, text=True)
    if proc.returncode:
        raise ToolError(f"wasm-tools {' '.join(args[:2])} failed:\n{proc.stderr.strip()}")
    return proc.stdout


def build(src: Path, out: Path | None = None, optimize: bool = True, keep_wat: bool = False,
          *, component: bool = False, frontend: str | None = None, compiler: str | None = None,
          module_root: Path | None = None, library: bool = False, exports: list[str] | None = None) -> Result:
    from . import component as cm
    source = src.read_text()
    if library and component:
        raise ValueError("library and component output are mutually exclusive")
    if library != bool(exports):
        raise ValueError("--library requires at least one --export FUNCTION; --export requires --library")
    project = Project(src, source, module_root)
    options = {"compiler": compiler} if compiler is not None else {}
    out = out or src.with_suffix(".wasm")
    destinations = [out]
    if keep_wat:
        destinations.append(out.with_suffix(".wat"))
    if component:
        destinations.append(out.with_suffix(".wit"))
    if library:
        destinations.extend([out.with_suffix(".js"), out.with_suffix(".d.ts")])
    resolved = [p.resolve() for p in destinations]
    if src.resolve() in resolved or len(set(resolved)) != len(resolved):
        raise ToolError("output paths must be distinct from the source and from each other")
    direct = selfhost.selected(compiler) and not keep_wat and not component
    if direct and frontend not in (None, "snek"):
        raise ValueError("the Snek compiler owns its frontend; --frontend cpython requires --compiler python")
    bindings = None
    direct_binary = None
    if direct:
        data = project.compile(**({"exports": exports} if library else {}))
        if library:
            header, metadata, data = data.split(b"\n", 2)
            if header != b"SNEK-LIBRARY-1":
                raise RuntimeError("Snek compiler did not return a library")
            bindings = json.loads(metadata)
        direct_binary = selfhost.core_binary(data)
    wat = None if direct else compile_to_wat(source, str(src), component=component, frontend=frontend, _project=project,
                                           **({"exports": exports} if library else {}), **options)
    if library and wat is not None:
        bindings = json.loads(wat.split("\n;; SNEK-BINDINGS ")[1].split("\n")[0])
    if any(s.path in resolved for s in project.sources.values() if s.text is not None):
        raise ToolError("output paths must not overwrite any project source")
    # Never use a neighboring source .wat as scratch space, nor leave a partial
    # artifact at the destination after an assembly/validation failure.
    with tempfile.TemporaryDirectory(prefix="snek-build-") as d:
        wat_path, binary = Path(d, "core.wat"), Path(d, "core.wasm")
        if direct:
            assert direct_binary is not None
            write_binary(direct_binary, binary, optimize)
        else:
            assert wat is not None
            wat_path.write_text(wat)
            assemble(wat_path, binary, optimize, compiler=compiler)
        if component:
            core = wasm_tools("print", str(binary))
            stdin = '(import "snek:component" "stdin"' in core
            features = cm.library.features(core)
            wat = cm.wrap(core)
            wat_path = Path(d, "component.wat")
            wat_path.write_text(wat)
            binary = Path(d, "component.wasm")
            wasm_tools("parse", str(wat_path), "-o", str(binary))
            wasm_tools("validate", "--features", "cm-async", str(binary))
            wit = Path(d, "command.wit")
            wit.write_text(cm.command_wit(stdin=stdin, features=features))
            wasm_tools("component", "targets", str(wit), "-w", "command", str(binary))
        shutil.copyfile(binary, out)
    if keep_wat:
        assert wat is not None
        out.with_suffix(".wat").write_text(wat)
    wit_path = None
    if component:
        wit_path = out.with_suffix(".wit")
        wit_path.write_text(cm.command_wit(stdin=stdin, features=features))
    js_path, dts_path = None, None
    if bindings is not None:
        js_path, dts_path = out.with_suffix(".js"), out.with_suffix(".d.ts")
        js_path.write_text(bindings["js"])
        dts_path.write_text(bindings["dts"])
    return Result(wat, out, wit_path, js_path, dts_path)
