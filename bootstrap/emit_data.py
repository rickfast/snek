"""CPython shim for runtime data embedded by the fixed compiler source linker.

This module is never included in the Snek compiler. build_emitter replaces its
imports with literal-returning Snek functions before constructing the seed.
"""
from pathlib import Path

RUNTIME = Path(__file__).resolve().parents[1] / "snekc"


def emit_memory_source() -> str:
    return (RUNTIME / "abi_memory.wat").read_text()


def emit_component_source() -> str:
    return (RUNTIME / "component_runtime.wat").read_text()


def emit_input_source() -> str:
    return (RUNTIME / "component_input.wat").read_text()
