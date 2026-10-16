# error: expected i32, got str
from typing import Callable
from snek import i32
def main() -> None:
    f: Callable[[i32], i32] = lambda x: "bad"
