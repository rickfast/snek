# error: tracebacks are opaque
from types import TracebackType

def compare(xs: list[TracebackType], ys: list[TracebackType]) -> bool:
    return xs == ys
