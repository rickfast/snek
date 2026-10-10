# error: 'return' in a finally block would swallow any in-flight exception
from snek import i32


def f() -> i32:
    try:
        return 1
    finally:
        return 2
