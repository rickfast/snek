# error: expected f64, got i32. Ints only mix into floats inside arithmetic
from snek import i32, f64


def half(x: f64) -> f64:
    return x / 2.0


def main() -> None:
    n: i32 = 3
    print(half(n))
