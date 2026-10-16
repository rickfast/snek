# error: key must return a number, bool, or str
def main() -> None:
    sorted([1, 2], key=lambda n: (n, n))
