from typing import Callable
from snek import i32, checkpoint, run


async def mark(log: list[i32], n: i32) -> i32:
    await checkpoint()
    log.append(n)
    return n


async def main() -> None:
    log: list[i32] = []
    values = [await mark(log, x * 10) for x in range(await mark(log, 4))
              if await mark(log, x) % 2 == 0]
    print(values, log)
    log.clear()
    mapping = {await mark(log, x): await mark(log, x + 10) for x in range(3)}
    print(mapping, log)
    log.clear()
    print([[await mark(log, x + y) for y in range(2)] for x in range(3)])
    print(log)
    funcs: list[Callable[[], i32]] = [lambda: x for x in range(3) if await mark(log, x) >= 0]
    print(funcs[0](), funcs[2]())
    outer = 99
    try:
        values = [10 // await mark(log, outer) for outer in [2, 0, 5]]
        print(values)
    except ZeroDivisionError:
        print("caught", outer)
    finally:
        await checkpoint()
        print("cleanup")


run(main())
