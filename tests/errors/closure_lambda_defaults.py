# error: lambda defaults
from typing import Callable
from snek import i32
def main() -> None:
    f: Callable[[i32], i32] = lambda x=1: x
