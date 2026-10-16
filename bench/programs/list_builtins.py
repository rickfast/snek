"""Sorting and numeric reductions on deterministic, unsorted integer lists."""
from snek import i32, i64


def main() -> None:
    checksum: i64 = 0
    for batch in range(50):
        values: list[i32] = []
        for i in range(10000):
            values.append((i * 7919 + batch * 1049) % 10007)
        ordered = sorted(values, reverse=batch % 2 == 0)
        checksum += sum(ordered) + min(ordered) + max(ordered)
        checksum += ordered[0] - ordered[9999]
    print(checksum)


if __name__ == "__main__":
    main()
