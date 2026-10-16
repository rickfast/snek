# error: local variable 'f' may be used before it is assigned
from snek import i32
def f() -> i32:
    return 1
def main() -> None:
    print(f())
    f = 2
