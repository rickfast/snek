# error: check `is not None` first
from snek import i32

def lookup(d: dict[str, i32] | None) -> i32:
    return d["key"]
