"""Pipe arbitrary bytes through native WASI 0.3 stdin/stdout streams."""
from snek import run
from snek.sys import stdin, stdout


async def main() -> None:
    data = await stdin.buffer.read()
    stdout.buffer.write(data)
    await stdout.drain()


run(main())
