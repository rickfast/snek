# error: async for comprehensions are not supported
async def main() -> None:
    values = (x async for x in [1, 2])
