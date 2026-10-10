# error: is Final and can only be assigned in __init__
from wpy import Final


class Config:
    name: Final[str]

    def __init__(self, name: str) -> None:
        self.name = name

    def rename(self, n: str) -> None:
        self.name = n
