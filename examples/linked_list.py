"""Optional references and flow-sensitive narrowing."""
from wpy import i32


class Node:
    value: i32
    next: "Node | None"

    def __init__(self, value: i32, next: "Node | None") -> None:
        self.value = value
        self.next = next


class Stack:
    head: Node | None = None
    size: i32 = 0

    def push(self, v: i32) -> None:
        self.head = Node(v, self.head)
        self.size += 1

    def pop(self) -> i32:
        node = self.head
        if node is None:
            return -1  # early exit: node is non-null below
        self.head = node.next
        self.size -= 1
        return node.value

    def total(self) -> i32:
        t = 0
        cur = self.head
        while cur is not None:
            t += cur.value
            cur = cur.next
        return t


def find(head: Node | None, v: i32) -> Node | None:
    cur = head
    while cur is not None and cur.value != v:
        cur = cur.next
    return cur


def main() -> None:
    s = Stack()
    for i in range(1, 6):
        s.push(i * 10)
    print("size", s.size, "total", s.total())
    print("pop", s.pop(), s.pop())
    hit = find(s.head, 20)
    if hit is not None:
        print("found", hit.value)
    print("missing is None:", find(s.head, 99) is None)
    label = "has head" if s.head is not None else "empty"
    print(label)
    while s.size > 0:
        s.pop()
    print("after drain:", s.pop(), s.head is None)


main()
