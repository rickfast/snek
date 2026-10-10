# error: may be None
from wpy import i32


class Node:
    value: i32 = 0
    next: "Node | None" = None


def second(n: Node) -> i32:
    return n.next.value
