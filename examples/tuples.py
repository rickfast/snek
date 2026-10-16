from snek import i32, i64


def pair(n: i32) -> tuple[str, i32]:
    return ("number", n)


def main() -> None:
    t = ("snek", 42, (True, 7))
    name, n, (flag, other) = t
    print(t, name, n, flag, other)
    print(t[0], t[-1], t[1:3], t[::-1], t[:0])
    print((), (5,), len(t), bool(t), bool(()), type(t).__name__)
    print(pair(9), repr(pair(9)), f"{pair(9)!r}")
    a = 1
    b = 2
    a, b = b, a
    print(a, b)
    homogeneous = (10, 20, 30, 20)
    for x in homogeneous:
        print(x, end=" ")
    print()
    for i in range(-5, 5):
        try:
            print(homogeneous[i])
        except IndexError as e:
            print(type(e).__name__, e)
    print(20 in homogeneous, 40 not in homogeneous)
    print(homogeneous.count(20), homogeneous.index(20, 2), homogeneous.index(20, -3, -1))
    try:
        print(homogeneous.index(999))
    except ValueError as e:
        print(e)
    print(list(homogeneous), tuple(t), t == ("snek", 42, (True, 7)), t != ("other", 42, (True, 7)))
    pairs = [("a", 1), ("b", 2)]
    for key, value in pairs:
        print(key, value)
    optional: tuple[str | None, i64] = (None, 42)
    print(optional, optional == (None, 42))
    wide: tuple[i64, i64] = (1, 2)
    print(wide)
    nested: list[tuple[str, list[i32]]] = [("one", [1, 2])]
    print(nested, nested == [("one", [1, 2])])


main()
