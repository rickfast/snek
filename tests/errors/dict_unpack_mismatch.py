# error: dictionary unpacking needs matching types
def main() -> None:
    numbers = {"key": 1}
    strings = {"key": "wrong"}
    print({**numbers, **strings})

main()
