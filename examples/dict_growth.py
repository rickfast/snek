from snek import i32


def main() -> None:
    d: dict[i32, i32] = {}
    # Colliding low hash bits, multiple growths and deletion tombstones.
    for i in range(300):
        d[i * 64] = i * i
    for i in range(0, 300, 3):
        del d[i * 64]
    for i in range(0, 300, 3):
        d[i * 64] = -i
    total = 0
    for k, v in d.items():
        total += v
    print(len(d), total, list(d))
    for i in range(300):
        print(d[i * 64], end=" ")
    print()
    for i in range(1000):
        d[-64] = i
        print(d.pop(-64), end=" ")
    print()
    print(len(d), d == dict(d))
    for i in range(300):
        k, v = d.popitem()
        print(k, v, end="; ")
    print()
    print(d)
    d[0] = 99
    print(d)
    d.clear()
    d[1] = 123
    print(d)


main()
