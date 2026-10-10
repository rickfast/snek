"""Pipeline: source -> AST -> typed IR -> WAT -> .wasm (via Binaryen's wasm-opt)."""

from __future__ import annotations

import ast
import os
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .check import check_module
from .emit_wat import emit_module
from .errors import CompileError

WASM_FEATURES = [
    "--enable-gc",
    "--enable-reference-types",
    "--enable-bulk-memory",
    "--enable-nontrapping-float-to-int",
    "--enable-sign-ext",
    "--enable-multivalue",
]


class ToolError(Exception):
    pass


@dataclass
class Result:
    wat: str
    wasm_path: Path | None = None


def compile_to_wat(source: str, filename: str = "<input>") -> str:
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError as e:
        err = CompileError(f"syntax error: {e.msg}")
        err.lineno, err.col = e.lineno, (e.offset or 1) - 1
        raise err from None
    return emit_module(check_module(tree))


def find_wasm_opt() -> list[str]:
    if env := os.environ.get("SNEK_WASM_OPT"):
        return shlex.split(env)
    if path := shutil.which("wasm-opt"):
        return [path]
    raise ToolError(
        "wasm-opt (Binaryen) not found. Install it (`brew install binaryen`, `apt install binaryen`, or "
        "`npm i -g binaryen`) or point SNEK_WASM_OPT at it.")


def assemble(wat_path: Path, wasm_path: Path, optimize: bool = True) -> None:
    cmd = [*find_wasm_opt(), str(wat_path), "-o", str(wasm_path), *WASM_FEATURES]
    if optimize:
        # Nothing with a GC type crosses the module boundary, so Binaryen may
        # assume a closed world and refine/merge/devirtualize types freely.
        cmd += ["-O3", "--closed-world"]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise ToolError(f"wasm-opt failed:\n{proc.stderr.strip()}")


def build(src: Path, out: Path | None = None, optimize: bool = True, keep_wat: bool = False) -> Result:
    source = src.read_text()
    wat = compile_to_wat(source, str(src))
    out = out or src.with_suffix(".wasm")
    wat_path = out.with_suffix(".wat")
    wat_path.write_text(wat)
    try:
        assemble(wat_path, out, optimize)
    finally:
        if not keep_wat:
            wat_path.unlink(missing_ok=True)
    return Result(wat, out)
