# error: Callable expects 1 arguments, got 0
from typing import Callable
from snek import i32
def use(f: Callable[[i32], i32]) -> None:
    f()
