# error: special method __add__ is not supported yet
from __future__ import annotations

class Item:
    def __add__(self, other: Item) -> Item:
        return self
