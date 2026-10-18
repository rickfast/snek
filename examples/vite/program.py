from snek import host, i64


@host("fetch_text")
async def fetch_text(url: str) -> str:
    pass


def greet(name: str) -> str:
    return "Hello, " + name + "!"


def twice(value: i64) -> i64:
    return value * 2


async def load(url: str) -> str:
    return (await fetch_text(url)).strip().upper()
