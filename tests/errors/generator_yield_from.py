# error: yield from is not supported yet
from snek import i32
from typing import Iterator


def values() -> Iterator[i32]:
    yield from [1, 2]
