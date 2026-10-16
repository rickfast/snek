# error: local variable 'value' may be used before it is assigned
from types import TracebackType

class Scope:
    def __enter__(self) -> str:
        return "scope"

    def __exit__(self, kind: type[BaseException] | None, error: BaseException | None,
                 tb: TracebackType | None) -> bool:
        return True

def main() -> None:
    with Scope():
        raise ValueError("fail")
        value = 3
    print(value)
