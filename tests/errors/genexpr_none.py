# error: cannot infer generator element type
def main() -> None:
    values = (None for x in range(2))
main()
