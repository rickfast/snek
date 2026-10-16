# error: expected str, got i32
def main() -> None:
    d = {"key": 1}
    print(d[1])

main()
