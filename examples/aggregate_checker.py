"""Collections and ordinary class bodies supported by the bootstrap checker."""
from __future__ import annotations
from snek import i32
from typing import Final


class Node:
    value: i32
    next: Node | None
    tags: list[str]

    def __init__(self, value: i32, next: Node | None = None) -> None:
        self.value = value
        self.next = next
        self.tags = []

    def total(self) -> i32:
        total = self.value
        node = self.next
        while node is not None:
            total += node.value
            node = node.next
        return total

    def label(self, prefix: str = "node") -> str:
        return prefix + ":" + str(self.value)

    def __str__(self) -> str:
        return self.label()


class Tagged(Node):
    tag: Final[str]

    def __init__(self, value: i32, tag: str) -> None:
        super().__init__(value)
        self.tag = tag
        self.tags.append(tag)

    def label(self, prefix: str = "node") -> str:
        return super().label(prefix) + ":" + self.tag


def describe(node: Node) -> str:
    return node.label()


def first(mapping: dict[str, i32]) -> i32:
    for key, value in mapping.items():
        return value
    return -1


def main() -> None:
    node = Node(3, Node(4))
    tagged = Tagged(5, "leaf")
    print(node.total(), describe(tagged), tagged.tags, str(node))
    nodes: list[Node] = [node, tagged]
    for item in nodes:
        print(item.label("seen"))
    maybe: Node | None = tagged
    if maybe is not None:
        print(maybe.value, isinstance(maybe, Tagged))
    values: list[i32] = [1, 2, 3]
    alias = values
    values += [4]
    values[0] += 5
    values.insert(1, 9)
    print(alias, values.pop(), values.count(9), values.index(9))
    print(values[::-1], values + [8], 2 * [7], 9 in values)
    rows: list[tuple[str, i32]] = [("a", 1), ("b", 2)]
    for key, value in rows:
        print(key, value)
    pair = ("first", (7, True))
    title, (number, flag) = pair
    print(title, number, flag, pair[-1], len(pair))
    mapping: dict[str, i32] = {"a": 1, "b": 2}
    mapping["a"] += 3
    mapping.setdefault("c", 8)
    print(mapping.get("z", -1), first(mapping), list(mapping.keys()))
    del mapping["b"]
    for key, value in mapping.items():
        print(key, value)
    print(mapping.copy() == mapping, "c" in mapping, mapping.popitem())
    nested: dict[tuple[str, i32], list[i32]] = {("key", 1): [2]}
    nested[("key", 1)].append(3)
    print(nested)
    for char in "a🐍z":
        print(char, end=";")
    print()


main()
