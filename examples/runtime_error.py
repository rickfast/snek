"""Runtime errors flush stdout, report on stderr and exit 1 (like an uncaught exception)."""
from snek import i32


def main() -> None:
    xs = [1, 2, 3]
    print("before")
    i: i32 = 3
    print(xs[i])
    print("never printed")


main()
