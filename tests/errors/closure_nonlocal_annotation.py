# error: a nonlocal variable cannot be annotated
from snek import i32
def main(x: i32) -> None:
    def f() -> None:
        nonlocal x
        x: i32 = 1
