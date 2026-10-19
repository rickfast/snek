# error: async generator expressions are not supported
from snek import i32, checkpoint
async def value(x: i32) -> i32:
    await checkpoint()
    return x
async def main() -> None:
    values = (await value(x) for x in range(2))
