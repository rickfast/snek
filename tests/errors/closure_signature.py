# error: explicit parameter types
from typing import Callable
from snek import i32
def use(f: Callable[..., i32]) -> None:
    pass
