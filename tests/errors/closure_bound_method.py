# error: bound methods are not first-class values
from snek import i32
class C:
    def f(self) -> i32:
        return 1
def main() -> None:
    f = C().f
