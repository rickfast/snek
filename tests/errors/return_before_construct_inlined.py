# error: this return in a base __init__ (reached through super().__init__()) would skip code that uses `self`
from snek import i32


class A:
    x: i32

    def __init__(self, x: i32) -> None:
        self.x = x
        if x < 0:
            return  # fine when A is built directly: every field is set
        print("positive", self.x)


class B(A):
    y: i32

    def __init__(self) -> None:
        super().__init__(1)
        self.y = 2
