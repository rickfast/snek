# error: cannot use default with multiple positional arguments
def main() -> None:
    max(1, 2, key=lambda n: n, default=3)
