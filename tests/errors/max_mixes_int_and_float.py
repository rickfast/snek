# error: max() argument: write 0.0 for a f64; CPython keeps 0 an int
from snek import f64


def clamp(x: f64) -> f64:
    return max(x, 0)
