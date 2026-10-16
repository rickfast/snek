# error: sum() start
def main() -> None:
    xs = [1, 2]
    print(sum(xs, 0 if len(xs) > 0 else 0.0))
