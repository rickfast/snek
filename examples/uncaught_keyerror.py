"""An uncaught KeyError("") reports `KeyError: ''`, because str() of a KeyError is repr(key)."""


def lookup(key: str) -> str:
    raise KeyError(key)


def main() -> None:
    print("looking up the empty key")
    lookup("")


main()
