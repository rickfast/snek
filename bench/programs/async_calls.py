"""Nested coroutine calls, with a cooperative checkpoint every hundred calls."""
from snek import i32, i64, run, checkpoint


async def transform(value: i32) -> i32:
    if value % 100 == 0:
        await checkpoint()
    return (value * 17) % 1009


async def main() -> None:
    checksum: i64 = 0
    for i in range(100000):
        checksum += await transform(i)
    print(checksum)


if __name__ == "__main__":
    run(main())
