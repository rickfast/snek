from snek import i32
from typing import Iterator, Generator


def numbers(n: i32) -> Iterator[i32]:
    print("start", n)
    try:
        for i in range(n):
            if i == 1:
                continue
            yield i
    finally:
        print("finish", n)


def recovering() -> Generator[str, None, None]:
    try:
        yield "ready"
    except ValueError as e:
        yield str(e)
    finally:
        print("cleanup")


def bad() -> Iterator[i32]:
    yield 1
    raise StopIteration("oops")


def main() -> None:
    a = numbers(4)
    b = numbers(2)
    print("created", iter(a) is a)
    print(next(a), next(b), next(a))
    b.close()
    for n in a:
        print(n)
    print(next(a, -1), next(a, -2))
    try:
        next(a)
    except StopIteration:
        print("stopped")
    a.close()
    c = recovering()
    print(c.send(None))
    print(c.throw(ValueError("injected")))
    c.close()
    d = bad()
    print(next(d))
    try:
        next(d)
    except RuntimeError as e:
        print(str(e))
    print(next(d, -9))


main()
