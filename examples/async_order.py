from snek import i32, run, checkpoint


async def value(n: i32, label: str = "value") -> i32:
    print(label, n)
    await checkpoint()
    return n


def show(n: i32) -> i32:
    print("sync", n)
    return n


def combine(a: i32, b: i32) -> i32:
    return a * 10 + b


async def returning() -> i32:
    try:
        return await value(8)
    finally:
        await checkpoint()
        try:
            raise ValueError("caught in finally")
        except ValueError as e:
            print(str(e))


async def main() -> None:
    print(combine(b=await value(2), a=show(1)))
    print(await value(label="keyword", n=await value(3)))
    print([show(4), await value(5), show(6)])
    print((await value(7), await value(8)))
    print({"a": await value(9), "b": await value(10)})
    print(show(11) + await value(12))
    print(True or await value(99) == 99)
    print(await returning())


run(main())
