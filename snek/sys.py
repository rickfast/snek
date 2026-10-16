"""CLI streams for Snek, with equivalent behavior under CPython.

Text writes return a character count; binary writes use list[i32] byte values.
flush() submits buffered data, while await drain() waits for host completion.
Binary stdin read(size=-1) and readline(size=-1) are async. Stream views share
their closed state; closing a view does not close the host's descriptor.
"""
from __future__ import annotations

import asyncio
import sys as _sys


class _Stream:
    def __init__(self, state=None):
        self._state = state if state is not None else {"closed": False}

    @property
    def closed(self) -> bool:
        return self._state["closed"]

    def _ensure_open(self) -> None:
        if self.closed:
            raise ValueError("I/O operation on closed stream")

    def close(self) -> None:
        if not self.closed:
            try:
                if isinstance(self, (BinaryOutput, TextOutput)):
                    self.flush()
            finally:
                self._state["closed"] = True

    def __enter__(self):
        self._ensure_open()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()


class BinaryOutput(_Stream):
    def __init__(self, name: str, state=None):
        super().__init__(state)
        self._name = name

    def write(self, data: list[int], /) -> int:
        self._ensure_open()
        if any(value < 0 or value > 255 for value in data):
            raise ValueError("byte values must be in range 0..255")
        stream = getattr(_sys, self._name)
        stream.flush()
        raw = bytes(data)
        offset = 0
        while offset < len(raw):
            count = stream.buffer.write(raw[offset:])
            if count is None or count <= 0 or count > len(raw) - offset:
                raise OSError(f"{self._name} write failed")
            offset += count
        return len(data)

    def flush(self) -> None:
        self._ensure_open()
        getattr(_sys, self._name).flush()

    async def drain(self) -> None:
        self.flush()
        await asyncio.sleep(0)


class TextOutput(_Stream):
    def __init__(self, name: str):
        super().__init__()
        self._name = name
        self.buffer = BinaryOutput(name, self._state)

    def write(self, text: str, /) -> int:
        # UTF-8 at the component boundary, independent of the host locale.
        self.buffer.write(list(text.encode("utf-8")))
        return len(text)

    def flush(self) -> None:
        self.buffer.flush()

    async def drain(self) -> None:
        await self.buffer.drain()


class BinaryInput(_Stream):
    async def read(self, size: int = -1, /) -> list[int]:
        self._ensure_open()
        _sys.stdout.flush()
        _sys.stderr.flush()
        # The shim must not block the asyncio event loop on pipe input.
        return list(await asyncio.to_thread(_sys.stdin.buffer.read, max(size, -1)))

    async def readline(self, size: int = -1, /) -> list[int]:
        self._ensure_open()
        _sys.stdout.flush()
        _sys.stderr.flush()
        return list(await asyncio.to_thread(_sys.stdin.buffer.readline, max(size, -1)))


class TextInput(_Stream):
    def __init__(self):
        super().__init__()
        self.buffer = BinaryInput(self._state)


stdin = TextInput()
stdout = TextOutput("stdout")
stderr = TextOutput("stderr")


def __getattr__(name: str):
    if name == "argv":
        return _sys.argv
    raise AttributeError(name)

__all__ = ["stdin", "stdout", "stderr", "argv"]
