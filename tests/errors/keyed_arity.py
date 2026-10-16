# error: key must take exactly one argument
from snek import i32
def key(a: i32, b: i32) -> i32:
    return a + b
def main() -> None:
    sorted([1, 2], key=key)
