from snek import f64


class Point:
    x: f64
    y: f64

    def __init__(self, x: f64, y: f64) -> None:
        self.x = x
        self.y = y
