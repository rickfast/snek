# error: only reference types (str, list, classes) can be None
from wpy import i32


def f(x: i32 | None) -> None:
    pass
