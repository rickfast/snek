from snek import i32
from typing import Iterator
from types import TracebackType


class Manager:
    def __enter__(self) -> i32:
        print("enter")
        return 8

    def __exit__(self, t: type[BaseException] | None, e: BaseException | None,
                 tb: TracebackType | None) -> bool:
        if t is not None:
            print("exit", t.__name__)
        else:
            print("exit normal")
        return False


def managed() -> Iterator[i32]:
    with Manager() as v:
        try:
            yield v
        finally:
            print("inner finally")


def pending_exception() -> Iterator[str]:
    try:
        raise ValueError("original")
    finally:
        yield "finally"
        try:
            raise TypeError("temporary")
        except TypeError as e:
            yield str(e)


def cleanup_loop() -> Iterator[i32]:
    for i in range(4):
        try:
            try:
                yield i
                if i == 0:
                    continue
                if i == 2:
                    break
            finally:
                yield i + 10
        finally:
            yield i + 20


def ignores_close() -> Iterator[i32]:
    try:
        yield 1
    except GeneratorExit:
        yield 2


def main() -> None:
    m = managed()
    m.close()  # never started: no enter or cleanup
    m = managed()
    print(next(m))
    m.close()
    print(next(m, -1))
    for x in cleanup_loop():
        print(x)
    p = pending_exception()
    print(next(p), next(p))
    try:
        next(p)
    except ValueError as e:
        print(str(e))
    q = ignores_close()
    print(next(q))
    try:
        q.close()
    except RuntimeError as e:
        print(str(e))
    q.close()
    print(next(q, -1))
    q = ignores_close()
    try:
        q.throw(StopIteration("unstarted"))
    except StopIteration as e:
        print(str(e))
    q = ignores_close()
    try:
        q.throw(ValueError("before start"))
    except ValueError as e:
        print(str(e))
    print(next(q, -1))


main()
