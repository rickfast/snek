from snek import i32, run, checkpoint
from typing import Callable, Iterator, Coroutine


def make_generator(start: i32) -> Callable[[], Iterator[i32]]:
    value = start

    def generate() -> Iterator[i32]:
        nonlocal value
        for i in range(3):
            value += 1
            yield value

    return generate


def callbacks() -> Iterator[Callable[[], i32]]:
    for n in range(3):
        yield lambda: n


def make_async(start: i32) -> Callable[[i32], Coroutine[None, None, i32]]:
    value = start

    async def add(n: i32) -> i32:
        nonlocal value
        await checkpoint()
        value += n
        return value

    return add


async def execute() -> None:
    f = make_async(20)
    print(await f(3), await f(4))
    value = 2
    callback: Callable[[i32], i32] = lambda x: value + x

    async def change() -> i32:
        nonlocal callback, value
        await checkpoint()
        value = 100
        callback = lambda x: x * 2
        return 5

    print(callback(await change()))
    print(callback(5))
    await checkpoint()
    print(value)


def main() -> None:
    g = make_generator(5)
    for x in g():
        print(x)
    for x in g():
        print(x)
    stream = callbacks()
    first = next(stream)
    second = next(stream)
    print(first(), second())
    third = next(stream)
    stream.close()
    print(first(), third())


main()
run(execute())
