"""List indexing and appends: sieve of Eratosthenes, repeated."""
from snek import i32


def sieve(n: i32) -> i32:
    flags: list[bool] = []
    for i in range(n + 1):
        flags.append(True)
    flags[0] = False
    flags[1] = False
    p = 2
    while p * p <= n:
        if flags[p]:
            m = p * p
            while m <= n:
                flags[m] = False
                m += p
        p += 1
    count = 0
    for f in flags:
        if f:
            count += 1
    return count


def main() -> None:
    total = 0
    for r in range(5):
        total += sieve(200000)
    print(total)


if __name__ == "__main__":
    main()
