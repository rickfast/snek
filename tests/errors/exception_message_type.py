# error: the exception message: expected str, got i32
def f() -> None:
    raise ValueError(42)
