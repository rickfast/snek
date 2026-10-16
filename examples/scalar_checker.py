"""Shared fixture for the Python compiler and the experimental Snek body checker."""
from snek import i32, i64, f32, f64


def fib(n: i32) -> i64:
    if n < 2:
        return i64(n)
    return fib(n - 1) + fib(n - 2)


def width(value: str | None) -> i32:
    if value is None:
        return 0
    return len(value)


def tick(label: str, value: i32) -> i32:
    print(label, end=";")
    return value


def pair(a: i32, b: i32 = 9) -> i32:
    return a * 10 + b


def nothing() -> None:
    print("side effect", end=";")


def main() -> None:
    print(fib(20), width(None), width("snek 🐍"))
    total: i64 = 0
    for i in range(9, 0, -2):
        if i == 5:
            continue
        total += i
    print(total)
    n = 0
    while True:
        n += 1
        if n == 4:
            break
    print(n)
    label: str | None = None
    print(label)
    label = "a\N{SNOWMAN}z"
    assert label is not None
    print(label[1], len(label), label == "a☃z", label < "b", repr(label))
    print(pair(b=tick("b", 2), a=tick("a", 1)), pair(3))
    print(str(nothing()), nothing())
    print(-7 // 3, -7 % 3, 7 / 2, 7 & 3, 1 << 5, ~7)
    small: f32 = abs(-0.5)
    number: f64 = 0.5
    number += 1
    print(small, number, number + 2, max(1.5, 2.5), min(-3, 4))
    print(f64(n), 0.5 + (1 if n == 4 else 2.5))
    print(bool(0), bool("yes"), not False, True and False or True)
    print(chr(9731), ord("🐍"), repr(chr(0x2028)))
    left: i64 = 2147483648
    count = 0
    for j in range(2147483646, left):
        count += 1
    print(count)


if __name__ == "__main__":
    main()
