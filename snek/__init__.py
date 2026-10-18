"""CPython shim for snek programs.

`from snek import i32, f64` makes a snek program runnable under regular CPython,
which the test suite uses as the reference implementation. Under CPython the
machine types are plain aliases (so there is no wrapping overflow); editors and
type checkers see them as int/float, while snekc enforces the real rules.
"""

from asyncio import run
from typing import Final, final
from functools import wraps
from inspect import iscoroutinefunction

from . import io, os, secrets, sys, time


async def checkpoint() -> None:
    """Cooperatively yield to the scheduler (WASI callback when componentized)."""
    from asyncio import sleep
    await sleep(0)


i32 = int
i64 = int
f32 = float
f64 = float


def host(name: str):
    """Declare a JavaScript host import for a Snek library.

    CPython has no JavaScript host; calls fail explicitly rather than silently
    executing the declaration's pass body. Tests can replace the function.
    """
    if not isinstance(name, str) or not name:
        raise ValueError("host import name must be a nonempty string")

    def decorate(function):
        if iscoroutinefunction(function):
            @wraps(function)
            async def unavailable_async(*args, **kwargs):
                raise RuntimeError(f"host import {name!r} requires a JavaScript host")
            return unavailable_async
        @wraps(function)
        def unavailable(*args, **kwargs):
            raise RuntimeError(f"host import {name!r} requires a JavaScript host")
        return unavailable
    return decorate


def read_bytes() -> list[i32]:
    """Read stdin to EOF without text decoding (core WASI target)."""
    import sys
    try:
        sys.stdout.flush()
        return list(sys.stdin.buffer.read())
    except OSError as error:
        raise RuntimeError("stdin read failed") from error


def write_bytes(data: list[i32]) -> None:
    """Write byte values to stdout, validating the entire list first."""
    import sys
    if any(value < 0 or value > 255 for value in data):
        raise ValueError("byte values must be in range 0..255")
    try:
        sys.stdout.flush()
        sys.stdout.buffer.write(bytes(data))
        sys.stdout.buffer.flush()
    except OSError as error:
        raise RuntimeError("stdout write failed") from error


__all__ = ["i32", "i64", "f32", "f64", "Final", "final", "host", "run", "checkpoint", "read_bytes", "write_bytes", "sys", "os", "time", "secrets", "io"]
