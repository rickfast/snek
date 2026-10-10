"""list methods, slicing, `in`, +, *, == and in-place += / *=."""

from snek import i32


class Node:
    name: str

    def __init__(self, name: str) -> None:
        self.name = name


def show(xs: list[i32]) -> None:
    print(xs, len(xs))


def slicing() -> None:
    xs = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9]
    show(xs[2:5])
    show(xs[:3])
    show(xs[7:])
    show(xs[-3:])
    show(xs[::3])
    show(xs[::-1])
    show(xs[8:2:-2])
    show(xs[20:])
    show(xs[:])
    flags = [True, False, True, True]
    print(flags[1:], flags[::-1])
    names = ["a", "b", "c"]
    copy = names[:]
    copy.append("d")
    print(names, copy)
    try:
        print(xs[::0])
    except ValueError as e:
        print("ValueError:", e)


def searching() -> None:
    xs = [5, 3, 5, 1, 5]
    print(xs.index(5), xs.index(5, 1), xs.index(5, -2), xs.count(5), xs.count(9), 3 in xs, 4 in xs, 4 not in xs)
    try:
        print(xs.index(5, 1, 2))
    except ValueError as e:
        print("ValueError:", e)
    words = ["x", "y"]
    try:
        print(words.index("z"))
    except ValueError as e:
        print("ValueError:", e)
    maybe: list[str | None] = ["a", None, "b"]
    print(None in maybe, maybe.index(None), maybe.count("b"))
    fs = [1.5, 2.0]
    print(2 in fs, 2.5 in fs, fs.index(2.0), fs.count(2))
    grid = [[1, 2], [3]]
    print([3] in grid, [4] in grid, grid.index([3]), grid == [[1, 2], [3]], grid != [[1, 2]])
    a = Node("a")
    nodes = [a, Node("b")]
    print(a in nodes, Node("a") in nodes, nodes.count(a))


def mutating() -> None:
    xs = [1, 2, 3]
    xs.insert(0, 0)
    xs.insert(-1, 99)
    xs.insert(100, 4)
    xs.insert(-100, -1)
    show(xs)
    xs.remove(99)
    show(xs)
    try:
        xs.remove(42)
    except ValueError as e:
        print("ValueError:", e)
    print(xs.pop(0), xs.pop(-1), xs.pop(1))
    show(xs)
    try:
        xs.pop(10)
    except IndexError as e:
        print("IndexError:", e)
    empty: list[i32] = []
    try:
        empty.pop(0)
    except IndexError as e:
        print("IndexError:", e)
    xs.extend([7, 8])
    xs.extend(xs)
    show(xs)
    xs.reverse()
    show(xs)
    ys = xs.copy()
    xs.clear()
    show(xs)
    show(ys)
    xs.append(1)
    show(xs)


def operators() -> None:
    a = [1, 2]
    b = a
    a += [3]
    print(a, b, a is b)
    a *= 2
    print(a, b)
    c = a + [9]
    print(c, a)
    print([0] * 4, 3 * ["x"], [1, 2] * 0, [] + [1], [1] + [])
    print([1, 2] == [1, 2], [1, 2] == [2, 1], [1] != [1, 1], ["é"] == ["é"])
    rows = [[0] * 3] * 2
    rows[0][0] = 5
    print(rows)
    nested = [[1], [2]]
    nested[0] += [10]
    print(nested)


def main() -> None:
    slicing()
    searching()
    mutating()
    operators()


main()
