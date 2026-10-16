# error: tracebacks are opaque
from types import TracebackType

def inspect(tb: TracebackType) -> None:
    print(tb.tb_lineno)
