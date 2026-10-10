# error: default for 'x': write 2.0 for a f64; CPython keeps 2 an int
from snek import f64


def scale(x: f64 = 2) -> f64:
    return x
