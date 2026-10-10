"""Classes: single inheritance, virtual dispatch, isinstance narrowing, Final fields."""
from wpy import i32, f64, Final


class Shape:
    name: Final[str]
    hits: i32 = 0

    def __init__(self, name: str) -> None:
        self.name = name

    def area(self) -> f64:
        return 0.0

    def describe(self) -> str:
        # Not overridden anywhere, so calls to it are devirtualized.
        self.hits += 1
        return f"{self.name} with area {self.area()}"


class Circle(Shape):
    r: f64

    def __init__(self, r: f64) -> None:
        self.name = "circle"
        self.r = r

    def area(self) -> f64:
        return 3.0 * self.r * self.r


class Rect(Shape):
    w: f64
    h: f64

    def __init__(self, w: f64, h: f64) -> None:
        self.name = "rect"
        self.w = w
        self.h = h
        print("made a rect", self.w, "x", self.h)  # uses self: runs after allocation

    def area(self) -> f64:
        return self.w * self.h


class Square(Rect):
    # Inherits Rect.__init__ (re-checked with self: Square) and Rect.area.
    def describe_side(self) -> str:
        return f"side {self.w}"


def total_area(shapes: list[Shape]) -> f64:
    total = 0.0
    for s in shapes:
        total += s.area()  # call_ref through the vtable
    return total


def main() -> None:
    shapes: list[Shape] = [Circle(1.0), Rect(2.0, 3.5), Square(2.0, 2.0)]
    for s in shapes:
        print(s.describe())
        if isinstance(s, Circle):
            print("  radius", s.r)  # s is narrowed to Circle here
        if isinstance(s, Square):
            print("  ", s.describe_side())
    print("total", total_area(shapes))
    first = shapes[0]
    print("hits", first.hits, first.name)
    print(shapes[1] is shapes[1], shapes[1] is shapes[2])


main()
