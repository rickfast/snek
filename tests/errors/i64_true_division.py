# error: / on i64 would lose precision as f64
from snek import i64, f64


def f(a: i64, b: i64) -> f64:
    return a / b
