# error: pair() missing required argument: 'b'
from snek import i32


def pair(a: i32, b: i32) -> i32:
    return a + b


def main() -> None:
    pair(1)
