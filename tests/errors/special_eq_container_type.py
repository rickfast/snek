# error: Child.__eq__ cannot accept Base
from __future__ import annotations

class Base:
    pass

class Child(Base):
    def __eq__(self, other: Child) -> bool:
        return True

def main() -> None:
    xs: list[Base] = [Child()]
    print(xs.count(Base()))
