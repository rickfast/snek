"""CPython shim for wpy programs.

`from wpy import i32, f64` makes a wpy program runnable under regular CPython,
which the test suite uses as the reference implementation. Under CPython the
machine types are plain aliases (so there is no wrapping overflow); editors and
type checkers see them as int/float, while wpyc enforces the real rules.
"""

from typing import Final, final

i32 = int
i64 = int
f32 = float
f64 = float

__all__ = ["i32", "i64", "f32", "f64", "Final", "final"]
