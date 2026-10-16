# error: key must accept i32, got str
from snek import i32
def key(s: str) -> i32:
    return len(s)
def main() -> None:
    max([1, 2], key=key)
