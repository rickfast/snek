# error: needs both __enter__ and __exit__ for with
class Item:
    def __enter__(self) -> str:
        return "item"

def main() -> None:
    with Item():
        pass
