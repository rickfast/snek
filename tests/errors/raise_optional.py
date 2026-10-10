# error: may be None (type ValueError | None); check `is not None` before raising it
def f(e: ValueError | None) -> None:
    raise e
