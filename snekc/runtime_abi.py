"""Runtime import contracts shared by reference and self-hosted code generation.

This module is valid Snek. The source linker includes it in the standalone
compiler; callers receive fresh dictionaries, never mutable global state.
Only ABI data is shared: compiler passes remain independent implementations.
"""
from __future__ import annotations


def abi_component_base_imports() -> dict[str, str]:
    return {
        "stdout": "(param i32) (result i32)",
        "stderr": "(param i32) (result i32)",
        "exit": "(param i32)",
        "stream-new": "(result i64)",
        "stream-write": "(param i32 i32 i32) (result i32)",
        "stream-drop": "(param i32)",
        "future-read": "(param i32 i32) (result i32)",
        "future-drop": "(param i32)",
        "set-new": "(result i32)",
        "set-drop": "(param i32)",
        "join": "(param i32 i32)",
        "task-return": "(param i32)",
        "task-cancel": "",
    }


def abi_component_input_imports() -> dict[str, str]:
    return {
        "stdin": "(param i32)",
        "stream-read": "(param i32 i32 i32) (result i32)",
        "stream-drop-reader": "(param i32)",
        "stream-cancel-read": "(param i32) (result i32)",
        "future-cancel-read": "(param i32) (result i32)",
    }


def abi_component_library_imports() -> dict[str, dict[str, str]]:
    return {
        "arguments": {"lib-arguments": "(param i32)"},
        "environment": {"lib-environment": "(param i32)"},
        "system-clock": {"lib-system-now": "(param i32)"},
        "monotonic-clock": {"lib-monotonic-now": "(result i64)"},
        "sleep": {"lib-wait-for": "(param i64) (result i32)",
                  "lib-subtask-cancel": "(param i32) (result i32)", "lib-subtask-drop": "(param i32)"},
        "random": {"lib-random": "(param i64 i32)"},
    }


def abi_component_arena_imports() -> dict[str, str]:
    return {"lib-reset-arena": "", "lib-alloc": "(param i32 i32) (result i32)"}


def abi_library_operations() -> dict[str, str]:
    return {
        "argv": "arguments", "getenv": "environment", "getenv-required": "environment",
        "time": "system-clock", "monotonic": "monotonic-clock", "sleep": "sleep",
        "token_bytes": "random", "token_hex": "random", "randbelow": "random",
    }


def abi_needs_arena(features: list[str]) -> bool:
    return "arguments" in features or "environment" in features or "random" in features


def abi_host_imports(features: list[str]) -> dict[str, str]:
    bindings: dict[str, str] = {}
    for feature in ("arguments", "environment"):
        if feature in features:
            prefix = "args" if feature == "arguments" else "environ"
            bindings[prefix + "_sizes_get"] = "(param i32 i32) (result i32)"
            bindings[prefix + "_get"] = "(param i32 i32) (result i32)"
    if "system-clock" in features or "monotonic-clock" in features:
        bindings["clock_time_get"] = "(param i32 i64 i32) (result i32)"
    if "sleep" in features:
        bindings["poll_oneoff"] = "(param i32 i32 i32 i32) (result i32)"
    if "random" in features:
        bindings["random_get"] = "(param i32 i32) (result i32)"
    return bindings
