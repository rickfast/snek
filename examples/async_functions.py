from snek import i32, run, checkpoint


async def value(n: i32) -> i32:
    print("start", n)
    await checkpoint()
    print("end", n)
    return n


async def failing() -> i32:
    try:
        await checkpoint()
        raise ValueError("async error")
    finally:
        await checkpoint()
        print("failing cleanup")


async def bad_stop() -> None:
    raise StopIteration("not an async return")


async def main() -> None:
    print(await value(2) + await value(3))
    print(False and await value(9) == 9)
    print(await value(4) if True else await value(99))
    for i in range(3):
        try:
            print(await value(i))
            if i == 1:
                continue
            if i == 2:
                break
        finally:
            await checkpoint()
            print("loop cleanup", i)
    try:
        await failing()
    except ValueError as e:
        await checkpoint()
        print(str(e))
    c = value(7)
    print(await c)
    try:
        await c
    except RuntimeError as e:
        print(str(e))
    try:
        await bad_stop()
    except RuntimeError as e:
        print(str(e))


run(main())
