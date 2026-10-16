# error: __eq__ requires
from __future__ import annotations
from snek import i32

class Item:
    def __eq__(self, other: Item) -> i32:
        return 1
