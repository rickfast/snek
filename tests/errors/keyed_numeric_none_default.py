# error: expected i32, got None
def main() -> None:
    min([1], key=lambda n: n, default=None)
