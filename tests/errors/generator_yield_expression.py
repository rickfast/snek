# error: yield expressions/send values are not supported
from snek import i32
from typing import Iterator


def values() -> Iterator[i32]:
    x = yield 1
