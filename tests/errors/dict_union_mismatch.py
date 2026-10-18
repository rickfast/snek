# error: dictionary union needs matching types
from snek import i32

def main() -> None:
    left: dict[str, i32] = {"key": 1}
    right: dict[str, str] = {"key": "wrong"}
    print(left | right)

main()
