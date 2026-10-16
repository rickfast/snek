# error: Callable values take positional arguments only
from snek import i32
def main() -> None:
    def f(n: i32) -> i32:
        return n
    f(n=2)
