# error: key must return a number, bool, or str
from snek import i32
async def key(n: i32) -> i32:
    return n
def main() -> None:
    sorted([1, 2], key=key)
