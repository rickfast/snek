# error: a str can only be repeated by an i32, got i64
from snek import i64


def main() -> None:
    n: i64 = 3
    print("ab" * n)


main()
