from snek import host


@host("fetch_text")
async def fetch_text(url: str) -> str:
    pass


async def load(url: str) -> str:
    try:
        return (await fetch_text(url)).strip().upper()
    except RuntimeError as error:
        return "Could not load: " + str(error)
