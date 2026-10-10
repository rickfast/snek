# error: 'args' is reserved on exception classes
class E(Exception):
    args: list[str]
