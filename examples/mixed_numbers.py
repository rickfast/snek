"""Ints mix into floats where CPython's result is a float anyway: arithmetic and comparisons."""
from snek import i32, f64


def scale(x: f64, by: f64 = 2.0) -> f64:
    return x * by


def main() -> None:
    n: i32 = 3
    x = 0.5
    print(x + n, n / 2, x * 2, 10 - x, x < n, n == 3.0)
    for c in [True, False]:
        # a conditional with an int branch is fine as an arithmetic operand...
        print(0.5 + (1 if c else 2.5), x < (1 if c else 2.5), abs(-1 if c else -2.5) * 2.0)
        y = 1.0
        y += 1 if c else 2.5
        print(y)
    # ...and anywhere the value is kept, it must already be a float
    print(scale(f64(n)), scale(1.5, by=0.5), f64(n) / 4.0)
    print(max(1.5, 2.5), min(n, 7), max(f64(n), 0.0))


main()
