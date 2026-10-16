from __future__ import annotations
from snek import i32


class Item:
    xs: list[Item]
    action: i32

    def __init__(self, xs: list[Item], action: i32) -> None:
        self.xs = xs
        self.action = action

    def __eq__(self, other: Item) -> bool:
        print("compare", self.action)
        if self.action == 1:
            self.action = 0
            for i in range(9):
                self.xs.append(other)
        elif self.action == 2:
            self.xs.clear()
        elif self.action == 3:
            self.xs.clear()
            return True
        elif self.action == 4:
            self.xs.clear()
            other.xs.clear()
        return False


def main() -> None:
    xs: list[Item] = []
    target = Item(xs, 0)
    xs.append(Item(xs, 1))
    print(target in xs, len(xs))
    xs.clear()
    xs.append(Item(xs, 1))
    print(xs.count(target), len(xs))
    xs.clear()
    xs.append(Item(xs, 2))
    print(target in xs, len(xs))
    xs.append(Item(xs, 3))
    xs.remove(target)
    print(len(xs))
    ys: list[Item] = []
    xs.append(Item(xs, 4))
    ys.append(Item(ys, 0))
    print(xs == ys, len(xs), len(ys))
    a, b = Item(xs, 0), Item(xs, 0)
    try:
        print((a, b).index(target, 1))
    except ValueError as error:
        print(error)
    print(None)


main()
