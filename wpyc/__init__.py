"""wpyc - compiler for wpy, a statically typed Python subset targeting WasmGC + WASI."""

from .driver import build, compile_to_wat
from .errors import CompileError

__all__ = ["build", "compile_to_wat", "CompileError"]
