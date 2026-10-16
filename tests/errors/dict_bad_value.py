# error: expected i32, got str
def main() -> None:
    d = {"key": 1}
    d["key"] = "wrong"

main()
