# error: __enter__ requires
from snek import i32

class Item:
    def __enter__(self, n: i32) -> i32:
        return n
