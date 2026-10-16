# error: condition must be bool, got i32
def main() -> None:
    print(sorted([1, 2], reverse=1))
