from snek import f64
from .point import Point


def distance_squared(a: Point, b: Point) -> f64:
    dx = a.x - b.x
    dy = a.y - b.y
    return dx * dx + dy * dy
