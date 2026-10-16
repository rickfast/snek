# error: get() needs an explicit default
def main() -> None:
    d = {"key": 1}
    print(d.get("absent"))

main()
