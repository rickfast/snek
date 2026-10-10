# error: may be used before it is assigned
from snek import i32


def f(flag: bool) -> i32:
    if flag:
        y = 1
    return y
