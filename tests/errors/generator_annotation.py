# error: a generator needs an Iterator[T]
from snek import i32


def values() -> i32:
    yield 1
