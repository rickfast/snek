# error: must assign self.radius before `self` is used
from snek import f64


class Circle:
    radius: f64

    def __init__(self, r: f64) -> None:
        self.log("creating")
        self.radius = r

    def log(self, msg: str) -> None:
        print(msg)
