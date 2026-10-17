"""Load built-in classes and helpers authored in runtime/*.snek.

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

from .runtime_sources import load_runtime_source

BUILTINS_SOURCE = load_runtime_source("exceptions.snek")
STR_METHODS_SOURCE = load_runtime_source("strings.snek", unicode_constants=True)
