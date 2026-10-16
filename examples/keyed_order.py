from snek import i32
from typing import Callable


def identity(n: i32) -> i32:
    return n


def negative(n: i32) -> i32:
    return -n


def argument_order() -> None:
    events: list[str] = []
    values = [3, 1, 2]

    def source() -> list[i32]:
        events.append("source")
        return values

    def key(n: i32) -> i32:
        events.append(f"key {n}")
        return n % 2

    def get_key() -> Callable[[i32], i32]:
        events.append("get key")
        return key

    def reverse() -> bool:
        events.append("reverse")
        return True

    def default() -> i32:
        events.append("default")
        return 99

    print(sorted(source(), key=get_key(), reverse=reverse()), events)
    events.clear()
    print(sorted(source(), reverse=reverse(), key=get_key()), events)
    events.clear()
    print(min(source(), key=get_key(), default=default()), events)
    events.clear()
    print(max(source(), default=default(), key=get_key()), events)
    events.clear()
    values.clear()
    print(min(source(), key=get_key(), default=default()), events)
    events.clear()
    print(sorted(source(), key=get_key(), reverse=True), events)


def snapshot() -> None:
    values = [3, 1, 2]
    seen: list[i32] = []

    def key(n: i32) -> i32:
        seen.append(n)
        if n == 3:
            values[1] = 99
            values.append(0)
        return n

    print(sorted(values, key=key), values, seen)
    seen.clear()
    print(sorted([7], key=key), seen)


def live_lists() -> None:
    values = [3, 2]
    seen: list[i32] = []

    def grow(n: i32) -> i32:
        seen.append(n)
        if n == 3 and len(values) == 2:
            for i in range(12):
                values.append(i - 5)
        return n

    print(min(values, key=grow), seen)
    values = [3, 2]
    seen.clear()
    print(max(values, key=grow), seen)

    def clear(n: i32) -> i32:
        values.clear()
        return n

    print(min(values, key=clear), values)
    values = [7, 9, 8]

    def remove_first(n: i32) -> i32:
        values.pop(0)
        return n

    print(max(values, key=remove_first), values)


def rebound_key() -> None:
    key_fn = identity

    def switch() -> bool:
        nonlocal key_fn
        key_fn = negative
        return False

    print(sorted([3, 1, 2], key=key_fn, reverse=switch()))
    key_fn = identity
    print(sorted([3, 1, 2], reverse=switch(), key=key_fn))

    def choose_source() -> list[i32]:
        nonlocal key_fn
        key_fn = negative
        return [3, 1, 2]

    key_fn = identity
    print(sorted(choose_source(), key=key_fn))


def exceptions() -> None:
    seen: list[i32] = []

    def fail(n: i32) -> i32:
        seen.append(n)
        if n == 2:
            raise ValueError("key failed")
        return -n

    values = [1, 2, 3]
    try:
        sorted(values, key=fail)
    except ValueError as e:
        print(e, seen, values)
    seen.clear()
    try:
        min(values, key=fail)
    except ValueError as e:
        print(e, seen, values)
    seen.clear()
    try:
        max(values, key=fail)
    except ValueError as e:
        print(e, seen, values)
    seen.clear()
    empty: list[i32] = []
    print(max(empty, key=fail, default=2), seen)


def nested() -> None:
    xs = [3, -2, 1]
    print(sorted(xs, key=lambda x: max([x, -x], key=lambda y: y)))
    print(sorted(xs, key=lambda x: min([2, 1], key=lambda y: y + x)))


argument_order()
snapshot()
live_lists()
rebound_key()
exceptions()
nested()
