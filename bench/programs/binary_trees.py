"""Allocation-heavy: the benchmarks-game binary trees (exercises the GC)."""
from snek import i32


class Tree:
    left: "Tree | None"
    right: "Tree | None"

    def __init__(self, left: "Tree | None", right: "Tree | None") -> None:
        self.left = left
        self.right = right


def make(depth: i32) -> Tree:
    if depth == 0:
        return Tree(None, None)
    return Tree(make(depth - 1), make(depth - 1))


def check(t: Tree) -> i32:
    l = t.left
    r = t.right
    if l is None or r is None:
        return 1
    return 1 + check(l) + check(r)


def main() -> None:
    max_depth = 14
    stretch = max_depth + 1
    print(f"stretch tree of depth {stretch} check: {check(make(stretch))}")
    long_lived = make(max_depth)
    d = 4
    while d <= max_depth:
        iterations = 1 << (max_depth - d + 4)
        total = 0
        for i in range(iterations):
            total += check(make(d))
        print(f"{iterations} trees of depth {d} check: {total}")
        d += 2
    print(f"long lived tree of depth {max_depth} check: {check(long_lived)}")


if __name__ == "__main__":
    main()
