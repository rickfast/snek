# error: parameter 'x' cannot be nonlocal
from snek import i32
def main(x: i32) -> None:
    def f(x: i32) -> None:
        nonlocal x
