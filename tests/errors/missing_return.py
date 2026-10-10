# error: can reach the end without returning
from snek import i32


def sign(x: i32) -> i32:
    if x > 0:
        return 1
    elif x < 0:
        return -1
