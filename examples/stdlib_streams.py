"""Text and binary CLI streams; runs under CPython and either WASI target."""
from snek import run, sys


async def main() -> None:
    print("buffered print")
    count = sys.stdout.write("hello, 世界 🐍\n")
    sys.stderr.write("diagnostic\n")
    count_bytes = sys.stdout.buffer.write([65, 0, 66, 10])
    await sys.stdout.drain()
    await sys.stderr.drain()
    print("characters:", count, "bytes:", count_bytes)
    try:
        sys.stdout.buffer.write([88, 256])
    except ValueError as error:
        print(str(error))
    sys.stdout.flush()


run(main())
