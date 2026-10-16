# error: only reference types (str, containers, classes) can be None
from snek import i32


def f(x: i32 | None) -> None:
    pass
