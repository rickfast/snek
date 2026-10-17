"""Invoke the independent Snek compiler. No Python compiler fallback."""
from __future__ import annotations
import os
from pathlib import Path
import re

from . import runner
from .errors import CompileError


def selected(compiler: str | None = None) -> bool:
    name = compiler if compiler is not None else os.environ.get("SNEK_COMPILER", "python")
    if name not in ("python", "snek"):
        raise ValueError(f"unknown compiler {name!r}; choose python or snek")
    return name == "snek"


def invoke(data: bytes, args: list[str]) -> bytes:
    binary = Path(os.environ.get("SNEK_COMPILER_WASM", str(Path(__file__).resolve().parents[1] / "bootstrap/build/compiler.wasm")))
    if not binary.is_file():
        raise RuntimeError("Snek compiler binary not found; run python bootstrap/bootstrap_compiler.py or set SNEK_COMPILER_WASM")
    result = runner.run_binary(binary, args, stdin=data)
    if result.code or result.stderr:
        diagnostic = result.stderr.decode("utf-8", errors="replace").strip()
        match = re.fullmatch(r"ValueError: line (\d+), column (\d+): (.*)", diagnostic, re.S)
        if match:
            error = CompileError(match[3])
            error.lineno, error.col = int(match[1]), int(match[2]) - 1
            raise error
        raise RuntimeError("Snek compiler failed: " + (diagnostic or f"exit {result.code}"))
    return result.stdout


def wat(source: str, *, component: bool = False) -> str:
    result = invoke(source.encode(), ["--wat", *(["--component"] if component else [])])
    text = result.decode("utf-8")
    if not text.startswith("(module"):
        raise RuntimeError("Snek compiler did not return core WAT")
    return text


def encode(wat: str) -> bytes:
    return core_binary(invoke(wat.encode(), ["--encode"]))


def compile(source: str) -> bytes:
    """Compile source directly, without a text/encode round trip."""
    return core_binary(invoke(source.encode(), []))


def core_binary(binary: bytes) -> bytes:
    if not binary.startswith(b"\0asm\1\0\0\0"):
        raise RuntimeError("Snek compiler did not return a core Wasm binary")
    return binary
