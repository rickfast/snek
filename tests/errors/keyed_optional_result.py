# error: key must return a number, bool, or str
def main() -> None:
    max([1, 2], key=lambda n: "a" if n == 1 else None)
