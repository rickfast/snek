# error: callable may be None
from typing import Callable
from snek import i32
def use(f: Callable[[i32], i32] | None) -> None:
    f(1)
