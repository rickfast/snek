# error: super() calls need `self`
class CustomError(Exception):
    def __init__(self) -> None:
        super().__init__(super().__str__())
