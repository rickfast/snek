# error: Point.__init__ must assign self.y before returning
from snek import i32


class Point:
    x: i32
    y: i32

    def __init__(self, x: i32) -> None:
        self.x = x
        if x < 0:
            return
        self.y = 0
