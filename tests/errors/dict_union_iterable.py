# error: dictionary union needs matching types
from snek import i32

def main() -> None:
    values: dict[str, i32] = {"key": 1}
    values |= [("key", 2)]

main()
