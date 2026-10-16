from snek import i32


def main() -> None:
    for size in [0, 1, 2, 3, 7, 8, 9, 31, 32, 33, 127, 128, 129, 513]:
        values: list[tuple[i32, i32]] = []
        for i in range(size):
            values.append(((i * 37) % 13, i))
        seen: list[i32] = []

        def key(row: tuple[i32, i32]) -> i32:
            seen.append(row[1])
            return row[0]

        for reverse in [False, True]:
            seen.clear()
            ordered = sorted(values, key=key, reverse=reverse)
            assert len(seen) == size
            assert len(ordered) == size
            for i in range(size):
                assert seen[i] == i
                assert values[i][1] == i
            for i in range(1, size):
                previous, current = ordered[i - 1], ordered[i]
                if previous[0] == current[0]:
                    assert previous[1] < current[1]
                elif reverse:
                    assert previous[0] > current[0]
                else:
                    assert previous[0] < current[0]
            checksum = 0
            for row in ordered:
                checksum += row[1] * (row[0] + 1)
            print(size, reverse, checksum)
        if size > 0:
            seen.clear()
            smallest = min(values, key=key)
            assert len(seen) == size
            print(smallest)
            seen.clear()
            largest = max(values, key=key)
            assert len(seen) == size
            print(largest)
    nan = 1e999 - 1e999
    floats = [(0, nan), (1, 1.0), (2, -1.0)]
    print(min(floats, key=lambda row: row[1])[0], max(floats, key=lambda row: row[1])[0])
    floats = [(0, 0.0), (1, -0.0), (2, 0.0)]
    print(min(floats, key=lambda row: row[1])[0], max(floats, key=lambda row: row[1])[0])
    print(sorted(floats, key=lambda row: row[1], reverse=True))


main()
