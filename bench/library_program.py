"""Library workloads for the JavaScript boundary benchmark (not a command)."""
from typing import Callable, Iterator
from snek import i32, host


def scalar(value: i32) -> i32:
    return value + 1


def text(value: str) -> str:
    return value


def values() -> list[i32]:
    return [7, 11, 13]


def mapping() -> dict[str, i32]:
    return {"value": 7}


def echo_list(value: list[i32]) -> list[i32]:
    return value


def echo_dict(value: dict[str, i32]) -> dict[str, i32]:
    return value


def numbers(n: i32) -> Iterator[i32]:
    for i in range(n):
        yield i


def callback(fn: Callable[[i32], i32], value: i32) -> i32:
    return fn(value)


@host("increment")
async def increment(value: i32) -> i32:
    pass


async def async_host(value: i32) -> i32:
    return await increment(value)
