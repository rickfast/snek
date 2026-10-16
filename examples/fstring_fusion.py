"""Fused integer formatting: boundaries, Unicode metadata, and evaluation order."""
from snek import i32, i64, f64, run, checkpoint


class Value:
    n: i32
    log: list[str]
    fail: bool

    def __init__(self, n: i32, log: list[str], fail: bool = False) -> None:
        self.n = n
        self.log = log
        self.fail = fail

    def __str__(self) -> str:
        self.log.append("render")
        self.n += 1
        if self.fail:
            raise ValueError("render failed")
        return f"value={self.n}"


def number(n: i32, log: list[str]) -> i32:
    log.append(f"number {n}")
    return n


def main() -> None:
    small: list[i32] = [-2147483648, -100, -10, -1, 0, 1, 9, 10, 99, 100, 2147483647]
    wide: list[i64] = [-9223372036854775808, -10000000000, -1, 0, 1, 9223372036854775807]
    for a in small:
        for b in wide:
            text = f"雪😀{a}:{b}!"
            print(text, len(text), text[1], text[-1], text[2:-1])
            print(f"{a}{b}", "[" + str(a) + str(b) + "]", f"{b!s}/{a!r}", f"{a}")
    text = "éß😀xy".upper()[::2]
    print(f"{text}{small[0]}{text}", len(f"{text}{small[0]}{text}"))
    decimal: f64 = 1.25
    optional: str | None = None
    print(f"{small[0]} {True} {decimal} {optional} {wide[-1]}")
    print(f"{small[-1]} {wide[0]:+025d} {wide[-1]:x} {decimal:.2f}")

    log: list[str] = []
    value = Value(7, log)
    print(f"{value.n}/{value}/{value.n}/{number(4, log)}")
    print(log)
    log.clear()
    print(str(number(1, log)) + str(value) + str(number(2, log)))
    print(log)
    log.clear()
    try:
        print(f"{number(3, log)} {Value(0, log, True)} {number(4, log)}")
    except ValueError as error:
        print(error)
    print(log)
    log.clear()
    try:
        print(f"{number(5, log)} {10 // number(0, log)} {number(6, log)}")
    except ZeroDivisionError as error:
        print(error)
    print(log)


async def later(n: i32) -> i32:
    await checkpoint()
    return n


async def async_main() -> None:
    print(f"雪{await later(-10)}:{await later(20)}😀")


main()
run(async_main())
