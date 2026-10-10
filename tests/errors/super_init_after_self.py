# error: super().__init__() must come before `self` is used
class Base:
    a: str

    def __init__(self, a: str) -> None:
        self.a = a


class Child(Base):
    b: str

    def __init__(self) -> None:
        self.b = "b"
        print(self.b)
        super().__init__("a")
