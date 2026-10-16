"""Three-stage generator pipeline with resumable loops and filtering."""
from typing import Iterator
from snek import i32, i64


def numbers(n: i32) -> Iterator[i32]:
    for i in range(n):
        yield i


def transform(source: Iterator[i32]) -> Iterator[i32]:
    for value in source:
        yield (value * 17) % 1009


def select(source: Iterator[i32]) -> Iterator[i32]:
    for value in source:
        if value % 3 != 0:
            yield value


def main() -> None:
    checksum: i64 = 0
    for value in select(transform(numbers(300000))):
        checksum += value
    print(checksum)


if __name__ == "__main__":
    main()
