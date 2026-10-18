# error: gather() requires matching non-None coroutine result types
from snek import gather, i32
async def number() -> i32:
    return 1
async def text() -> str:
    return "a"
gather(number(), text())
