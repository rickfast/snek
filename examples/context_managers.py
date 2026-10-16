from __future__ import annotations
from types import TracebackType
from snek import i32


class Manager:
    name: str
    log: list[str]
    suppress: bool
    fail_enter: bool
    fail_exit: bool

    def __init__(self, name: str, log: list[str], suppress: bool = False,
                 fail_enter: bool = False, fail_exit: bool = False) -> None:
        self.name = name
        self.log = log
        self.suppress = suppress
        self.fail_enter = fail_enter
        self.fail_exit = fail_exit

    def __enter__(self) -> str:
        self.log.append("enter " + self.name)
        if self.fail_enter:
            raise ValueError("enter failed")
        return self.name

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None,
                 tb: TracebackType | None) -> bool:
        if exc_type is None:
            self.log.append("exit " + self.name + " normal")
            assert exc is None and tb is None
        else:
            assert exc is not None and tb is not None
            self.log.append("exit " + self.name + " " + exc_type.__name__ + ": " + str(exc))
            print(exc_type is ValueError, isinstance(exc, ValueError))
        if self.fail_exit:
            raise RuntimeError("exit failed")
        return self.suppress


class PlainManager:
    log: list[str]

    def __init__(self, log: list[str]) -> None:
        self.log = log

    def __enter__(self) -> i32:
        self.log.append("plain enter")
        return 42

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None,
                 tb: TracebackType | None) -> None:
        self.log.append("plain exit")


def returning(log: list[str]) -> i32:
    with PlainManager(log) as value:
        return value


def swallowed(log: list[str]) -> i32:
    with Manager("suppressed", log, True):
        raise ValueError("caught")
    return 7


def main() -> None:
    log: list[str] = []
    b = "not entered"
    with Manager("outer", log) as a, Manager("inner", log) as b:
        log.append(a + " " + b)
    print(a, b, log)
    log.clear()
    print(returning(log), swallowed(log), log)
    log.clear()
    for i in range(3):
        with PlainManager(log):
            if i == 0:
                continue
            if i == 1:
                break
        log.append("unreachable")
    print(log)
    log.clear()
    try:
        with Manager("outer", log), Manager("inner", log, fail_enter=True):
            log.append("unreachable")
    except ValueError as error:
        print(error)
    print(log)
    log.clear()
    with Manager("outer", log, True):
        with Manager("inner", log, fail_exit=True):
            raise ValueError("body")
        log.append("unreachable")
    print(log)
    log.clear()
    try:
        with Manager("fail normal exit", log, fail_exit=True):
            pass
    except RuntimeError as error:
        print(error)
    print(log)
    log.clear()
    try:
        raise ValueError("original")
    except ValueError:
        with PlainManager(log):
            try:
                raise
            except ValueError as error:
                print(error)
    print(log)


main()
