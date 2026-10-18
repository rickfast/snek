# error: async for comprehensions are not supported yet
from snek import run
async def main() -> None:
    print([x async for x in [1, 2]])
run(main())
