"""String formatting, concatenation and comparison."""
from snek import i32


def main() -> None:
    total = 0
    longest = ""
    for i in range(600000):
        s = f"item {i}: {i * 3 % 7} / {i // 2}"
        if i % 1000 == 0:
            s = s + "!"
        total += len(s)
        if s > longest:
            longest = s
    print(total, longest)


if __name__ == "__main__":
    main()
