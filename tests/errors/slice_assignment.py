# error: slice assignment is not supported yet
from snek import i32


def main() -> None:
    xs = [1, 2, 3]
    xs[0:2] = [9]
    print(xs)


main()
