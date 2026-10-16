# error: Item.__eq__ cannot accept Item | None
from __future__ import annotations

class Item:
    def __eq__(self, other: Item) -> bool:
        return True

def main() -> None:
    item: Item | None = None
    print(Item() == item)
