from snek import i32, i64, f32, f64
from typing import Callable


def increment(n: i32) -> i32:
    return n + 1


def apply(f: Callable[[i32], i32], n: i32) -> i32:
    return f(n)


def counter(start: i32) -> tuple[Callable[[], i32], Callable[[i32], None]]:
    value = start

    def get() -> i32:
        return value

    def add(n: i32) -> None:
        nonlocal value
        value += n

    return get, add


def late() -> Callable[[], i32]:
    def get() -> i32:
        return value

    value = 7
    value = 9
    return get


def transitive(value: i32) -> Callable[[], Callable[[], i32]]:
    def middle() -> Callable[[], i32]:
        return lambda: value

    return middle


def factorial() -> Callable[[i32], i32]:
    def fact(n: i32) -> i32:
        if n <= 1:
            return 1
        return n * fact(n - 1)

    return fact


def parity() -> Callable[[i32], bool]:
    def even(n: i32) -> bool:
        if n == 0:
            return True
        return odd(n - 1)

    def odd(n: i32) -> bool:
        if n == 0:
            return False
        return even(n - 1)

    return even


def loop_captures() -> list[Callable[[], i32]]:
    out: list[Callable[[], i32]] = []
    for i in range(4):
        out.append(lambda: i)
    return out


def types() -> None:
    big: i64 = 9000000000
    text = "captured"
    small: f32 = 1.5
    wide: f64 = 2.5
    flag = True
    items = [3, 4]
    a: Callable[[], i64] = lambda: big
    b: Callable[[], str] = lambda: text
    c: Callable[[], f32] = lambda: small
    d: Callable[[], f64] = lambda: wide
    e: Callable[[], bool] = lambda: flag
    f: Callable[[], list[i32]] = lambda: items
    print(a(), b(), c(), d(), e(), f())


def higher_order() -> Callable[[i32], Callable[[i32], i32]]:
    return lambda x: lambda y: x + y


def void_call(print: Callable[[i32], None]) -> None:
    print(50)


def optional_capture(value: str | None) -> Callable[[], str]:
    def read() -> str:
        snapshot = value
        if snapshot is not None:
            return snapshot.upper()
        return "empty"
    return read


def main() -> None:
    f = increment
    print(f(3), apply(increment, 5), apply(lambda n: n * 2, 8))
    print(f is increment, f == increment)
    get, add = counter(10)
    other, other_add = counter(20)
    add(3)
    other_add(8)
    print(get(), other(), late()(), transitive(42)()())
    print(factorial()(6), parity()(8), parity()(7))
    for closure in loop_captures():
        print(closure())
    ops: dict[str, Callable[[i32], i32]] = {"inc": increment}
    print(ops["inc"](9))
    maybe: Callable[[i32], i32] | None = increment
    if maybe is not None:
        print(maybe(10))
    types()
    print(higher_order()(5)(6))
    noop: Callable[[], None] = lambda: None
    noop()
    void_call(lambda x: print(x + 1))
    print(optional_capture("value")(), optional_capture(None)())


main()
