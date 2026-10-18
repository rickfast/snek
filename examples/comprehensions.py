from typing import Callable, Iterator
from snek import i32, i64, f64


def numbers() -> Iterator[i32]:
    yield 2
    yield 4


def mark(log: list[i32], n: i32) -> i32:
    log.append(n)
    return n


class Box:
    values: list[i32]
    funcs: list[Callable[[], i32]]
    shadowed: list[Callable[[i32], i32]]

    def __init__(self, n: i32) -> None:
        self.values = [i + n for i in range(n)]
        self.funcs = [lambda: i + n for i in range(n)]
        self.shadowed = [lambda n: n + i for i in range(2)]
        n += 10

    def scaled(self) -> list[i32]:
        return [x * len(self.values) for x in self.values]


class Child(Box):
    def __init__(self, n: i32) -> None:
        super().__init__(n)


def closures() -> None:
    x = 99
    funcs: list[Callable[[], i32]] = [lambda: x for x in range(3)]
    print(x, funcs[0](), funcs[1](), funcs[2]())
    batches: list[list[Callable[[], i32]]] = []
    for stop in range(2, 5):
        batch: list[Callable[[], i32]] = [lambda: x for x in range(stop)]
        batches.append(batch)
    print(batches[0][0](), batches[1][0](), batches[2][0]())
    n = 1
    def bump() -> i32:
        nonlocal n
        n += 1
        return n
    print([n + bump() for i in range(3)], n)
    print([[x + y for y in range(2)] for x in range(3)])


def main() -> None:
    x = [1, 2, 3]
    print([x * x for x in x], x)
    print([range for range in range(3)])
    print([x for x in range(5, 0, -2)])
    log: list[i32] = []
    print([mark(log, x + y) for x in range(mark(log, 3))
           if mark(log, x) != 1 for y in range(mark(log, 2)) if y > 0])
    print(log)
    log.clear()
    print({mark(log, x % 2): mark(log, x + 10) for x in range(5)})
    print(log)
    print([a + b for a, b in [(1, 2), (3, 4)]])
    print([c + "!" for c in "a🐍é"])
    print([n * 2 for n in (2, 3, 4)])
    print([n + 1 for n in numbers()])
    data = {"a": 1, "b": 2, "c": 3}
    print([key for key in data if data[key] > 1])
    print({key: value * 2 for key, value in data.items() if value != 2})
    print([n for n in data.values()])
    empty: list[i64] = [1 for _ in range(0)]
    wide: list[i64] = [1 for _ in range(2)]
    floating: list[f64] = [1.0 for _ in range(2)]
    nullable: dict[str, str | None] = {key: None for key in data}
    nested: list[list[i32]] = [[] for _ in range(2)]
    print(empty, wide, floating, nullable)
    nested[0].append(7)
    print(nested)
    print(Box(3).values, Box(3).scaled(), Box(3).funcs[0]())
    print(Child(3).values, Child(3).funcs[0](), Child(3).shadowed[0](20))
    closures()
    # Exceptions leave the outer binding intact and release dictionary iterators.
    value = 42
    try:
        bad = [10 // value for value in [2, 0, 5]]
        print(bad)
    except ZeroDivisionError:
        print(value)
    try:
        bad_dict = {k: 10 // (v - 2) for k, v in data.items()}
        print(bad_dict)
    except ZeroDivisionError:
        data["d"] = 4
        print(data)


main()
