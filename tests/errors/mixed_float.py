# error: mixed operand types i64 and f64
from wpy import i64, f64


def f(a: i64, b: f64) -> f64:
    return a + b
