# error: pair() got multiple values for argument 'a'
from snek import i32


def pair(a: i32, b: i32) -> i32:
    return a + b


def main() -> None:
    pair(1, a=2)
