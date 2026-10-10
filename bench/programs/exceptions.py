"""Raising and catching exceptions in a loop."""
from snek import i32, i64


class Invalid(ValueError):
    code: i32

    def __init__(self, code: i32) -> None:
        self.code = code
        super().__init__("invalid")


def parse(i: i32) -> i32:
    if i % 3 == 0:
        raise Invalid(i)
    return i


def main() -> None:
    ok: i64 = 0
    bad: i64 = 0
    for i in range(1000000):
        try:
            ok += parse(i)
        except Invalid as e:
            bad += e.code
    print(ok, bad)


if __name__ == "__main__":
    main()
