"""Bounded binary copy with typed, first-class streams and explicit cleanup."""
from snek import io, run, sys


async def copy(source: io.BinaryInput, sink: io.BinaryOutput) -> None:
    while True:
        chunk = await source.read(8192)
        if len(chunk) == 0:
            break
        sink.write(chunk)
        await sink.drain()


async def main() -> None:
    with sys.stdin.buffer as source, sys.stdout.buffer as sink:
        await copy(source, sink)


run(main())
