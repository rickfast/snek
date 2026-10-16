# error: only generator.send(None) is supported
from snek import i32
from typing import Iterator


def values() -> Iterator[i32]:
    yield 1


values().send(2)
