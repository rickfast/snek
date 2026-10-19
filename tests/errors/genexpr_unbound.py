# error: local variable 'y' may be used before it is assigned
def main() -> None:
    y = 9
    values = (x for x in range(3) if y > 0 for y in range(2))
main()
