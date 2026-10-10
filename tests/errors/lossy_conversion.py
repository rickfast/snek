# error: Implicit i64 -> i32 conversion could lose information
from wpy import i32, i64


def f(x: i64) -> i32:
    return x
