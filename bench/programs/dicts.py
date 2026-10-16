"""Tuple-key counting, dictionary growth, lookups, and ordered iteration."""
from snek import i32, i64


def main() -> None:
    counts: dict[tuple[i32, i32], i32] = {}
    for i in range(200000):
        key = (i % 128, (i // 128) % 64)
        counts[key] = counts.get(key, 0) + 1
    checksum: i64 = 0
    for key, count in counts.items():
        x, y = key
        checksum += (x + y) * count
    print(len(counts), checksum)


if __name__ == "__main__":
    main()
