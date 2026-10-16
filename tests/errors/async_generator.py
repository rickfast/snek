# error: async generators are not supported yet
from snek import i32
from typing import Iterator


async def values() -> Iterator[i32]:
    yield 3
