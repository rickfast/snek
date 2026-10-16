# error: sorted() key must be a non-optional Callable
from snek import i32

def negative(v: i32) -> i32:
    return -v

def main() -> None:
    print(sorted([1, 2], key=3))
