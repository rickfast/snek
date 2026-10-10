# error: default values must be literals
from snek import i32


def f(xs: list[i32] = []) -> None:
    pass
