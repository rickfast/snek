# error: key must return a number, bool, or str
def main() -> None:
    min([1, 2], key=lambda n: None)
