# error: 'first_bad' can reach the end without returning a i32
from snek import i32


def first_bad(xs: list[i32]) -> i32:
    i = 0
    while True:
        try:
            if 10 // xs[i] > 3:
                return i
        except ZeroDivisionError:
            break
        i += 1
