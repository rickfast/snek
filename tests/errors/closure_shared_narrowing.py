# error: may be None
def main() -> None:
    value: str | None = "hello"
    def reset() -> None:
        nonlocal value
        value = None
    if value is not None:
        print(value.upper())
