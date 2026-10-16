from snek import i32, f64, run, checkpoint
from typing import Iterator, Coroutine


def floats() -> Iterator[f64]:
    yield 1.5
    yield 2.5


def optional() -> Iterator[str | None]:
    yield None
    yield "yes"


def pairs() -> Iterator[tuple[str, i32]]:
    for i in range(3):
        yield ("number", i)


def nested() -> Iterator[list[i32]]:
    yield [1, 2]
    yield [3]


def consume(xs: Iterator[i32]) -> i32:
    total = 0
    for x in xs:
        total += x
    return total


def numbers() -> Iterator[i32]:
    yield 2
    yield 3


async def produce() -> Iterator[i32]:
    await checkpoint()
    return numbers()


async def strings() -> str:
    await checkpoint()
    return "async string"


async def main() -> None:
    for x in floats():
        print(x)
    for s in optional():
        if s is None:
            print("none")
        else:
            print(s)
    for label, n in pairs():
        print(label, n)
    for xs in nested():
        print(xs)
    print(consume(numbers()), list(numbers()))
    print(list(await produce()))
    c: Coroutine[None, None, str] = strings()
    print(await c)


run(main())
