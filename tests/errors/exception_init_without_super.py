# error: InputError.__init__ must call super().__init__(message)
class InputError(ValueError):
    field: str

    def __init__(self, field: str) -> None:
        self.field = field
