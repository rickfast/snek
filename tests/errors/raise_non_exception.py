# error: exceptions must derive from BaseException, and Point does not
class Point:
    pass


def f() -> None:
    raise Point
