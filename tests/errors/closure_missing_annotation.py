# error: parameter 'n' needs a type annotation
from snek import i32
def main() -> None:
    def f(n) -> i32:
        return n
