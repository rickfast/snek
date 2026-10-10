"""Loops, break/continue, assert narrowing, conditional expressions, recursion."""
from wpy import i32, i64


class Item:
    key: str
    weight: i32

    def __init__(self, key: str, weight: i32) -> None:
        self.key = key
        self.weight = weight


class Tagged(Item):
    def heavier(self, other: Item) -> bool:
        return self.weight > other.weight


def ackermann(m: i64, n: i64) -> i64:
    if m == 0:
        return n + 1
    if n == 0:
        return ackermann(m - 1, 1)
    return ackermann(m - 1, ackermann(m, n - 1))


def first_over(items: list[Item | None], limit: i32) -> Item | None:
    for it in items:
        if it is None:
            continue
        if it.weight > limit:
            return it
    return None


def countdown(n: i32) -> str:
    out = ""
    for i in range(n, 0, -2):
        out += str(i) + " "
    return out


def search(target: i32) -> i32:
    i = 0
    while True:
        if i * i >= target:
            break
        i += 1
    return i


def main() -> None:
    print("ackermann(2, 3) =", ackermann(2, 3))
    items: list[Item | None] = [Item("a", 3), None, Tagged("b", 9), Item("c", 12)]
    hit = first_over(items, 5)
    assert hit is not None, "nothing over the limit"
    print("first over 5:", hit.key)  # narrowed by the assert
    print("none over 50:", first_over(items, 50) is None)
    print(countdown(9), search(50))
    w = hit.weight if isinstance(hit, Tagged) else -1
    print("tagged weight:", w)
    t = Tagged("z", 1)
    other = items[0]
    if other is not None:
        print("heavier:", t.heavier(other))
    total = 0
    for i in range(10):
        if i % 3 == 0:
            continue
        if i == 8:
            break
        total += i
    print("total", total)
    nick: str | None = None
    print("nick:", nick)
    nick = "ziggy"
    print("nick:", nick, len(nick))


main()
