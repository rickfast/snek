# error: lambda parameter count
def main() -> None:
    min([1, 2], key=lambda: 0)
