"""CPython shim for snek programs.

`from snek import i32, f64` makes a snek program runnable under regular CPython,
which the test suite uses as the reference implementation. Under CPython the
machine types are plain aliases (so there is no wrapping overflow); editors and
type checkers see them as int/float, while snekc enforces the real rules.
"""

from typing import Final, final

i32 = int
i64 = int
f32 = float
f64 = float

__all__ = ["i32", "i64", "f32", "f64", "Final", "final"]
