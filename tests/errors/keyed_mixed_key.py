# error: key result: this can be an int at run time
def main() -> None:
    sorted([1, 2], key=lambda n: 1 if n == 1 else 2.0)
