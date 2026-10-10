# error: overrides must keep the same parameter names
from snek import i32


class Base:
    def scale(self, factor: i32) -> i32:
        return factor


class Child(Base):
    def scale(self, k: i32) -> i32:
        return k * 2
