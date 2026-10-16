# error: condition must be bool
def main() -> None:
    sorted([1], key=lambda n: n, reverse=1)
