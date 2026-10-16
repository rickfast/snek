from snek import i32, run, checkpoint
from typing import Iterator


class Base:
    def numbers(self, n: i32) -> Iterator[i32]:
        yield n

    async def value(self, n: i32) -> i32:
        await checkpoint()
        return n


class Child(Base):
    def numbers(self, n: i32) -> Iterator[i32]:
        for i in range(n):
            yield i * 2

    async def value(self, n: i32) -> i32:
        await checkpoint()
        return await super().value(n) + 1


def use(b: Base) -> None:
    for n in b.numbers(3):
        print(n)


async def other(b: Base) -> None:
    print(await b.value(5))


use(Base())
use(Child())
run(other(Base()))
run(other(Child()))
