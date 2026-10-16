# error: this operation requires a non-empty tuple with one element type
from snek import i32

def main(i: i32) -> None:
    t = ("key", 1)
    print(t[i])
