from typing import Callable, Iterator
from snek import i32, i64, checkpoint, run


def mark(log: list[i32], n: i32) -> i32:
    log.append(n)
    return n


def source(log: list[i32]) -> list[i32]:
    log.append(100)
    return [1, 2, 3]


def consume(values: Iterator[i32]) -> i32:
    total = 0
    for value in values:
        total += value
    return total


def fail_source() -> list[i32]:
    raise ValueError("outer evaluated")


def stop() -> i32:
    raise StopIteration("inner stopped")


def nested(log: list[i32]) -> Iterator[i32]:
    try:
        yield mark(log, 7)
        yield mark(log, 8)
    finally:
        log.append(99)


class Box:
    values: Iterator[i32]

    def __init__(self, n: i32) -> None:
        self.values = (n + x for x in range(n))
        n += 10


class Child(Box):
    def __init__(self, n: i32) -> None:
        super().__init__(n)


async def fetch(log: list[i32]) -> list[i32]:
    await checkpoint()
    return source(log)


async def bound(log: list[i32]) -> i32:
    await checkpoint()
    return mark(log, 3)


async def asynchronous() -> None:
    log: list[i32] = []
    values = (mark(log, x) for x in await fetch(log))
    print(log, next(values), log)
    await checkpoint()
    print(list(values), log)
    values = (mark(log, x) for x in range(await bound(log)))
    print(log, list(values))


def main() -> None:
    log: list[i32] = []
    values = (mark(log, x * 10) for x in source(log) if mark(log, x) != 2)
    print(log)
    print(next(values), log)
    print(list(values), log, next(values, -1))
    print(consume(x * x for x in range(4)))
    print(list((x, y) for x in range(3) for y in range(x) if y != 1))
    print(list(a + b for a, b in [(1, 2), (3, 4)]))
    print(list(c + "!" for c in "a🐍é"))
    print(list(n * 2 for n in (2, 3, 4)))
    print(list(range for range in range(3)))
    print(list(x for x in range(5, 0, -2)))
    print(list(list(x + y for y in range(2)) for x in range(3)))
    log.clear()
    loops = (mark(log, x + y) for x in range(mark(log, 2))
             if mark(log, x) >= 0 for y in range(mark(log, 2)) if y > 0)
    print(log, next(loops), log)
    print(list(loops), log)
    try:
        unused = (x for x in fail_source())
        print("unreachable")
    except ValueError as e:
        print(str(e))
    stopped = (stop() for _ in range(1))
    try:
        print(next(stopped))
    except RuntimeError as e:
        print(str(e), next(stopped, -1))
    original = [1, 2]
    frozen = (x for x in original)
    original.append(3)
    original = [9]
    print(list(frozen), original)
    n = 3
    bounds = (x + n for x in range(n))
    n = 10
    print(list(bounds))
    later: i32
    deferred = (later for _ in range(2))
    later = 42
    print(list(deferred))
    funcs: Iterator[Callable[[], i32]] = (lambda: x for x in range(3))
    first = next(funcs)
    second = next(funcs)
    print(first(), second())
    last = next(funcs)
    print(first(), second(), last())
    funcs.close()
    wide: Iterator[i64] = (1 for _ in range(2))
    empty: Iterator[list[i32]] = ([] for _ in range(2))
    optional: Iterator[str | None] = (None for _ in range(2))
    print(list(wide), list(empty), list(optional))
    print(list(Box(3).values), list(Child(2).values))
    # The outer iterator is acquired now, but its body is still lazy.
    inner = nested(log)
    outer = (x * 2 for x in inner)
    print(next(inner), next(outer))
    outer.close()
    print(next(inner, -1), log)
    # Closing or throwing into a fresh generator does not execute its body.
    log.clear()
    fresh = (mark(log, x) for x in source(log))
    fresh.close()
    print(log, next(fresh, -1))
    fresh = (mark(log, x) for x in source(log))
    try:
        fresh.throw(ValueError("injected"))
    except ValueError as e:
        print(str(e), log, next(fresh, -1))
    value = 77
    bad = (10 // value for value in [2, 0, 5])
    print(next(bad))
    try:
        print(next(bad))
    except ZeroDivisionError:
        print(value, next(bad, -1))
    # Dictionary iterators remember size at creation, not at first next().
    data = {"a": 1, "b": 2}
    keys = (key for key in data)
    data["c"] = 3
    try:
        print(next(keys))
    except RuntimeError as e:
        print(str(e), next(keys, "done"))
    items = (key + str(value) for key, value in data.items())
    data["a"] = 10
    print(list(items))
    keys = (key for key in data.keys())
    del data["a"]
    data["d"] = 4
    print(list(keys))
    vals = (value for value in data.values())
    print(next(vals))
    data.clear()
    try:
        print(next(vals))
    except RuntimeError as e:
        print(str(e))
    data["z"] = 9
    print(list(key for key in data))


main()
run(asynchronous())
