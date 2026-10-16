# error: unsupported dictionary key type list[i32]
from snek import i32

def main() -> None:
    d: dict[list[i32], str] = {}

main()
