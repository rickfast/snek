# error: overrides Base.f with a different signature
from snek import i32, i64


class Base:
    def f(self, x: i32) -> i32:
        return x


class Child(Base):
    def f(self, x: i64) -> i32:
        return 0
