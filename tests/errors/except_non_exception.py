# error: 'Point' is not an exception class
class Point:
    pass


def f() -> None:
    try:
        pass
    except Point:
        pass
