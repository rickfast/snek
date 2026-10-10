# error: list.index() on list[Node] is not supported
class Node:
    pass


def main() -> None:
    a = Node()
    nodes = [a]
    print(nodes.index(a))


main()
