# error: nonlocal 'x' must precede its use or assignment
from snek import i32
def main(x: i32) -> None:
    def f() -> None:
        print(x)
        nonlocal x
