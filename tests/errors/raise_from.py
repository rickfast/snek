# error: `raise ... from ...` is not supported yet
def f() -> None:
    try:
        pass
    except KeyError as e:
        raise ValueError("bad") from e
