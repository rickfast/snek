"""Fixed-width numerics with Python division semantics and explicit conversions."""
from snek import i32, i64, f32, f64


def fib(n: i32) -> i64:
    a: i64 = 0
    b: i64 = 1
    for _ in range(n):
        t = a + b
        a = b
        b = t
    return a


def collatz_steps(start: i64) -> i32:
    n = start
    steps = 0
    while n != 1:
        if n % 2 == 0:
            n = n // 2
        else:
            n = 3 * n + 1
        steps += 1
    return steps


def gcd(a: i64, b: i64) -> i64:
    while b != 0:
        r = a % b
        a = b
        b = r
    return abs(a)


def main() -> None:
    print("fib(90) =", fib(90))
    print("collatz(27) =", collatz_steps(27))
    print("gcd =", gcd(1071, -462))
    # Python floor-division semantics on fixed-width ints
    for a in [7, -7]:
        for b in [2, -2]:
            print(a, "//", b, "=", a // b, " ", a, "%", b, "=", a % b)
    # i32 widens implicitly to i64 and f64; narrowing is explicit
    small: i32 = 40000
    big = i64(small) * 50000  # the literal adopts i64 from the other operand
    print("big", big, "as f64", f64(big), "back", i32(f64(small) / 3.0))
    print("ratio", small / 7, "floor", 22.0 // 7.0, "fmod", -7.5 % 2.0)
    print(min(3, -4), max(2.5, 1.0), abs(-12), abs(-0.5))
    print(0.1 + 0.2, 1.0 / 3.0, 2.0 / 3.0, 1e16, 1.5e-7, 123456789.125, -0.0)
    x: f32 = 0.5
    print("f32 * 2 =", x * f32(2))
    print(i64(1) << 40, 255 & 15 | 64, 7 ^ 2, -16 >> 2, ~5)


main()
