# error: bare `raise` in a finally block is not supported
def f() -> None:
    try:
        raise ValueError("v")
    except ValueError:
        try:
            raise KeyError("k")
        finally:
            raise
