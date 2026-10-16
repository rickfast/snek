# error: format width and precision must fit in i32
def main() -> None:
    print(f"{1:999999999999999999999d}")
