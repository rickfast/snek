# component-error: gather() is not yet supported for components
from snek import checkpoint, gather, i32, run
from typing import Callable


class Box:
    value: i32

    def __init__(self, value: i32) -> None:
        self.value = value


async def number(value: i32) -> i32:
    for i in range(value):
        await checkpoint()
    return value


async def box(value: i32) -> Box:
    await checkpoint()
    return Box(value)


async def optional(value: str | None) -> str | None:
    await checkpoint()
    return value


async def items(value: i32) -> list[i32]:
    await checkpoint()
    return [value]


async def callback(value: i32) -> Callable[[], i32]:
    def get() -> i32:
        return value
    await checkpoint()
    return get


async def main() -> None:
    print(await gather(number(3), number(0), number(1)))
    boxes = await gather(box(1), box(2))
    print(boxes[0].value, boxes[1].value)
    print(await gather(optional(None), optional("🐍")))
    print(await gather(items(1), items(2)))
    nested = await gather(gather(number(2), number(1)), gather(number(4)))
    print(nested)
    callbacks = await gather(callback(10), callback(20))
    print(callbacks[0](), callbacks[1]())


run(main())
