from typing import Callable


def saved() -> Callable[[], str]:
    try:
        raise ValueError("gone")
    except ValueError as caught_error_payload:
        return lambda: str(caught_error_payload)


print(saved()())
