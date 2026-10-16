# error: cannot infer the element type of []
def main() -> None:
    sorted([], key=lambda n: n)
