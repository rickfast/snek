# error: f-string format specs must be literal
def main() -> None:
    width = 6
    print(f"{1:{width}d}")
