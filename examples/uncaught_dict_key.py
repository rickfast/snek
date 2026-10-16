from snek import i32


def main() -> None:
    d: dict[tuple[str, i32], str] = {}
    print(d[("missing", 42)])


main()
