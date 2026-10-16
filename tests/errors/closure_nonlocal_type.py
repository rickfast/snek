# error: expected i32, got str
from snek import i32
def main(x: i32) -> None:
    def f() -> None:
        nonlocal x
        x = "bad"
