# error: expected Callable[[i32], i32], got Callable[[str], str]
from typing import Callable
from snek import i32
def text(s: str) -> str:
    return s
def main() -> None:
    f: Callable[[i32], i32] = text
