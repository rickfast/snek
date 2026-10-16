from __future__ import annotations
from types import TracebackType
from snek import i32


class Scope:
    name: str
    log: list[str]
    suppress: bool
    fail: bool

    def __init__(self, name: str, log: list[str], suppress: bool = False, fail: bool = False) -> None:
        self.name = name
        self.log = log
        self.suppress = suppress
        self.fail = fail

    def __enter__(self) -> tuple[i32, i32]:
        self.log.append(self.name + " enter")
        return 10, 20

    def __exit__(self, kind: type[BaseException] | None, error: BaseException | None,
                 tb: TracebackType | None) -> bool:
        self.log.append(self.name + (" error" if error is not None else " normal"))
        if self.fail:
            raise ValueError("cleanup")
        return self.suppress


class Child(Scope):
    def __enter__(self) -> tuple[i32, i32]:
        self.log.append("child enter")
        return super().__enter__()

    def __exit__(self, kind: type[BaseException] | None, error: BaseException | None,
                 tb: TracebackType | None) -> bool:
        self.log.append("child exit")
        return super().__exit__(kind, error, tb)


class NoValue:
    def __enter__(self) -> None:
        print("enter None")

    def __exit__(self, kind: type[BaseException] | None, error: BaseException | None,
                 tb: TracebackType | None) -> None:
        print("exit None")


def fail_return() -> i32:
    raise ValueError("return expression")


def return_path(log: list[str], suppress: bool, fail: bool) -> i32:
    with Scope("return", log, suppress, fail):
        return fail_return() if suppress else 7
    return 8


def main() -> None:
    with NoValue():
        assigned = 42
    print(assigned)
    log: list[str] = []
    scopes: list[Scope] = [Child("derived", log), Scope("base", log)]
    for scope in scopes:
        with scope as (x, y):
            print(x, y)
    print(log)
    log.clear()
    target: list[i32] = []
    with Scope("binding", log, True) as (first, target[0]):
        log.append("unreachable")
    print(log)
    log.clear()
    print(return_path(log, False, False), return_path(log, True, False), log)
    log.clear()
    try:
        print(return_path(log, False, True))
    except ValueError as error:
        print(error, log)
    log.clear()
    for i in range(3):
        try:
            with Scope("loop", log):
                try:
                    if i == 0:
                        continue
                    break
                finally:
                    log.append("inner finally")
        finally:
            log.append("outer finally")
    print(log)
    log.clear()
    try:
        raise ValueError("original")
    except ValueError as original:
        with Scope("suppression", log, True):
            raise RuntimeError("temporary")
        try:
            raise
        except ValueError as again:
            print(original is again)
    print(log)


main()
