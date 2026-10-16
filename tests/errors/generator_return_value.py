# error: declared `-> None` but returns a value
from snek import i32
from typing import Iterator


def values() -> Iterator[i32]:
    yield 1
    return 2
