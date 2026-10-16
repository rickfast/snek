# error: got multiple values for 'key'
def main() -> None:
    sorted([1], key=lambda n: n, key=None)
