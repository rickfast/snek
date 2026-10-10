# error: default for 'n': expected i32, got str
from snek import i32


def f(n: i32 = "three") -> None:
    pass
