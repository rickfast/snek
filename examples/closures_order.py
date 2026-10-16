from snek import i32
from collections.abc import Callable


def original(x: i32) -> i32:
    print("original", x)
    return x + 1


def replacement(x: i32) -> i32:
    print("replacement", x)
    return x + 10


def pair(a: i32, b: i32) -> None:
    print(a, b)


def main() -> None:
    f = original
    value = 1

    def change() -> i32:
        nonlocal f, value
        f = replacement
        value = 100
        print("change")
        return 3

    def read() -> i32:
        return value

    print(f(change()))
    print(f(3))
    value = 2
    print(value + change(), read())
    value = 7
    pair(b=value, a=change())
    values = [0]

    def index() -> i32:
        nonlocal value
        value = 50
        return 0

    value = 9
    values[index()] = value
    print(values, value)
    value = 3
    print(value * str(change()))
    funcs: list[Callable[[i32], i32]] = [original]

    def select() -> Callable[[i32], i32]:
        print("select")
        return funcs[0]

    def mutate() -> i32:
        funcs[0] = replacement
        print("mutate")
        return 5

    print(select()(mutate()), funcs[0](4))
    print(funcs == [replacement], replacement in funcs, funcs.count(replacement))

    def shadow() -> i32:
        value = 8
        return value

    print(shadow(), value)


main()
