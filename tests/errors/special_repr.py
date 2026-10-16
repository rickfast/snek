# error: needs __repr__
class Item:
    def __str__(self) -> str:
        return "item"

def main() -> None:
    print(f"{Item()!r}")
