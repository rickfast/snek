# error: await needs a coroutine
from snek import i32


async def value() -> i32:
    return await 3
