# error: module state cannot be captured
from snek import i32
if __name__ == "__main__":
    x = 1
    def f() -> i32:
        nonlocal x
        return x
