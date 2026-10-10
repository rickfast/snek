"""Recursive calls and integer arithmetic."""
from snek import i32


def fib(n: i32) -> i32:
    if n < 2:
        return n
    return fib(n - 1) + fib(n - 2)


def main() -> None:
    print(fib(30))


if __name__ == "__main__":
    main()
