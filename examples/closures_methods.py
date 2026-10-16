from snek import i32
from typing import Callable


class Base:
    def callback(self) -> Callable[[], i32]:
        return lambda: 0


class Counter(Base):
    value: i32

    def __init__(self, value: i32) -> None:
        self.value = value

    def callback(self) -> Callable[[], i32]:
        def add() -> i32:
            self.value += 1
            return self.value
        return add


class Holder:
    fn: Callable[[i32], i32]

    def __init__(self, fn: Callable[[i32], i32]) -> None:
        self.fn = fn


def main() -> None:
    obj: Base = Counter(10)
    f = obj.callback()
    print(f(), f())
    h = Holder(lambda x: x + 5)
    print(h.fn(3))
    h.fn = lambda x: x * 2
    print(h.fn(3))


main()
