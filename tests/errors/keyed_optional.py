# error: key must be a non-optional Callable
from snek import i32
from typing import Callable
def main(key: Callable[[i32], i32] | None) -> None:
    sorted([1, 2], key=key)
