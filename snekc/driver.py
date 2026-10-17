"""Pipeline: source -> AST -> typed IR -> WAT -> native binary -> optional wasm-opt."""

from __future__ import annotations

import os
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


def compile_to_wat(source: str, filename: str = "<input>", *, component: bool = False,
                   frontend: str | None = None, compiler: str | None = None) -> str:
    if selfhost.selected(compiler):
        if frontend not in (None, "snek"):
            raise ValueError("the Snek compiler owns its frontend; --frontend cpython requires --compiler python")
        return selfhost.wat(source, component=component)
    selected = source_frontend.select(frontend)
    tree = source_frontend.parse_source(source, filename, frontend=selected)
    return emit_module(check_module(tree, frontend=selected), component=component)


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
          *, component: bool = False, frontend: str | None = None, compiler: str | None = None) -> Result:
    from . import component as cm
    source = src.read_text()
    options = {"compiler": compiler} if compiler is not None else {}
    out = out or src.with_suffix(".wasm")
    destinations = [out]
    if keep_wat:
        destinations.append(out.with_suffix(".wat"))
    if component:
        destinations.append(out.with_suffix(".wit"))
    resolved = [p.resolve() for p in destinations]
    if src.resolve() in resolved or len(set(resolved)) != len(resolved):
        raise ToolError("output paths must be distinct from the source and from each other")
    direct = selfhost.selected(compiler) and not keep_wat and not component
    if direct and frontend not in (None, "snek"):
        raise ValueError("the Snek compiler owns its frontend; --frontend cpython requires --compiler python")
    wat = None if direct else compile_to_wat(source, str(src), component=component, frontend=frontend, **options)
    # Never use a neighboring source .wat as scratch space, nor leave a partial
    # artifact at the destination after an assembly/validation failure.
    with tempfile.TemporaryDirectory(prefix="snek-build-") as d:
        wat_path, binary = Path(d, "core.wat"), Path(d, "core.wasm")
        if direct:
            write_binary(selfhost.compile(source), binary, optimize)
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
    return Result(wat, out, wit_path)
