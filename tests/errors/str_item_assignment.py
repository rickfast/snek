# error: str does not support item assignment (strings are immutable)
def main() -> None:
    s = "abc"
    s[0] = "x"
    print(s)


main()
