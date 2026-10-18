# error: dictionary unpacking requires a dictionary
def main() -> None:
    d = [("key", 1)]
    copied = {**d}

main()
