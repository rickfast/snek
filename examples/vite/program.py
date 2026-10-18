from snek import HostObject, host, i64
from typing import Callable, Coroutine


@host("fetch_text")
async def fetch_text(url: str) -> str:
    pass


def greet(name: str) -> str:
    return "Hello, " + name + "!"


def twice(value: i64) -> i64:
    return value * 2


async def load(url: str) -> str:
    return (await fetch_text(url)).strip().upper()


def identity(value: HostObject) -> HostObject:
    return value


def apply(fn: Callable[[str], str], text: str) -> str:
    return fn(text)


def make_greeter() -> Callable[[str], str]:
    return greet


def notify(fn: Callable[[str], None], text: str) -> None:
    fn(text)


async def apply_async(fn: Callable[[str], Coroutine[None, None, str]], text: str) -> str:
    return await fn(text)
