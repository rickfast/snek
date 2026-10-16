# error: tuples are immutable
def main() -> None:
    t = (1, 2)
    t[0] = 3

main()
