# error: closures in __init__ are not supported yet
from typing import Callable
from snek import i32
class C:
    f: Callable[[], i32]
    def __init__(self) -> None:
        self.f = lambda: 1
