"""Built-in classes, written in snek itself.

This source is parsed and declared ahead of every user module, so the
exception hierarchy goes through the same checker and emitter as user classes:
`class MyError(ValueError)` is ordinary single inheritance, `except
LookupError` is a `ref.test`, and the runtime (prelude.wat) throws instances of
these classes for IndexError, ZeroDivisionError and friends.

The message lives in a hidden field (`__message`), so user code reads it the
CPython way, through `str(e)`. It is None when the exception was created
without an argument, which is how `str(KeyError())` ('') differs from
`str(KeyError(""))` ("''"). The constructor parameter is spelled `__msg` so
that `ValueError(message=...)` is rejected, as it is in CPython, and the
checker only lets user code pass a str for it.
"""

BUILTINS_SOURCE = '''
class BaseException:
    __message: Final[str | None]

    def __init__(self, __msg: str | None = None) -> None:
        self.__message = __msg


class Exception(BaseException):
    pass


class ArithmeticError(Exception):
    pass


class ZeroDivisionError(ArithmeticError):
    pass


class OverflowError(ArithmeticError):
    pass


class AssertionError(Exception):
    pass


class LookupError(Exception):
    pass


class IndexError(LookupError):
    pass


class KeyError(LookupError):
    pass


class RuntimeError(Exception):
    pass


class NotImplementedError(RuntimeError):
    pass


class ValueError(Exception):
    pass


class TypeError(Exception):
    pass
'''
