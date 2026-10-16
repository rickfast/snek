# error: expected i32
from snek import i32


async def value() -> i32:
    return 3


def wrong() -> i32:
    return value()
