# error: await is only allowed inside async def
from snek import checkpoint


def wrong() -> None:
    await checkpoint()
