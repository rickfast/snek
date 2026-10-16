# error: dictionary unpacking (**d) is not supported
def main() -> None:
    d = {"key": 1}
    copied = {**d}

main()
