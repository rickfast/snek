# error: unsupported dictionary key type tuple[str, list[i32]]
from snek import i32

def main() -> None:
    d: dict[tuple[str, list[i32]], str] = {}

main()
