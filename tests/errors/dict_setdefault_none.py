# error: setdefault() needs an explicit default
def main() -> None:
    d = {"key": "value"}
    d.setdefault("absent")

main()
