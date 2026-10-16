from snek import i32
from typing import Callable


def early() -> None:
    def read() -> i32:
        return later

    def typename() -> str:
        return type(later).__name__

    try:
        read()
    except NameError as exc:
        print(type(exc).__name__, str(exc))
    try:
        typename()
    except NameError:
        print("type read checked")
    later = 12
    print(read())


def caught() -> Callable[[], str]:
    try:
        raise ValueError("hello")
    except ValueError as exc:
        def message() -> str:
            return str(exc)
        print(message())
        return message


def cleanup() -> None:
    saved: list[Callable[[], str]] = []
    for i in range(2):
        try:
            raise ValueError("loop")
        except ValueError as err:
            saved.append(lambda: str(err))
            if i == 0:
                continue
            break
    for f in saved:
        try:
            print(f())
        except NameError:
            print("cleared")


def finally_mutates() -> Callable[[], i32]:
    value = 1
    try:
        return lambda: value
    finally:
        value = 5


early()
try:
    print(caught()())
except NameError as error:
    print(type(error).__name__, str(error))
cleanup()
print(finally_mutates()())
