from snek import i32, checkpoint, run
from typing import Callable


async def main() -> None:
    events: list[str] = []
    offset = 2

    async def source() -> list[i32]:
        events.append("source")
        await checkpoint()
        return [5, 1, 3]

    async def choose_key() -> Callable[[i32], i32]:
        events.append("choose key")
        await checkpoint()
        return lambda n: abs(n - offset)

    async def reverse() -> bool:
        events.append("reverse")
        await checkpoint()
        return True

    async def fallback() -> i32:
        events.append("fallback")
        await checkpoint()
        return 99

    print(sorted(await source(), key=await choose_key(), reverse=await reverse()), events)
    events.clear()
    print(min(await source(), default=await fallback(), key=await choose_key()), events)
    print(max([5, 1, 3], key=lambda n: n + offset))


run(main())
