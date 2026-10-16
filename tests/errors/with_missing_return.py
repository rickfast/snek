# error: can reach the end without returning
from types import TracebackType
from snek import i32

class Scope:
    def __enter__(self) -> str:
        return "scope"

    def __exit__(self, kind: type[BaseException] | None, error: BaseException | None,
                 tb: TracebackType | None) -> bool:
        return True

def run() -> i32:
    with Scope():
        raise ValueError("fail")
