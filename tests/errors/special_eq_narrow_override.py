# error: Child.__eq__ cannot accept Base
from __future__ import annotations

class Base:
    pass

class Child(Base):
    def __eq__(self, other: Child) -> bool:
        return True

def compare(a: Base, b: Base) -> bool:
    return a == b
