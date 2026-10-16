from __future__ import annotations
from snek import i32, i64, f64, Final


class Node:
    name: str
    children: dict[str, Node]
    point: Final[tuple[i32, i32]]

    def __init__(self, name: str) -> None:
        self.name = name
        self.children = {}
        self.point = (0, 0)


def maybe_dict(present: bool) -> dict[str, list[i32]] | None:
    return {"a": [1, 2]} if present else None


def maybe_tuple(present: bool) -> tuple[str, i32] | None:
    return ("a", 1) if present else None


def early(d: dict[i32, i32]) -> tuple[i32, i32]:
    for key, value in d.items():
        try:
            return key, value
        finally:
            print("leaving", key)
    return -1, -1


def main() -> None:
    root = Node("root")
    child = Node("child")
    root.children["child"] = child
    print(root.children["child"].name, root.point)
    found = root.children.get("child")
    if found is not None:
        print(found.name)
    print(root.children.get("absent") is None)
    print(root.children == {"child": child}, child in root.children.values())
    for key, node in root.children.items():
        print(key, node.name)
    pairs = [("a", 1), ("b", 2)]
    root.name, pairs[0] = ("renamed", ("first", 10))
    print(root.name, pairs)
    for flag in [False, True]:
        d = maybe_dict(flag)
        t = maybe_tuple(flag)
        print(d, repr(d), t, repr(t))
        print([d], [t], (d, t))
        print(d == maybe_dict(flag), t == maybe_tuple(flag))
        print(d != maybe_dict(not flag), t != maybe_tuple(not flag))
        if d is not None:
            d["a"] += [3]
            print(d)
        if t is not None:
            text, number = t
            print(text, number)
    optional_values: dict[str, tuple[str, i32] | None] = {"empty": None, "pair": ("a", 1)}
    print(optional_values, optional_values.get("missing"), optional_values.pop("missing", None))
    strings = {"s": "text"}
    fallback: str | None = None
    print(strings.get("missing", fallback), strings.pop("missing", fallback))
    nullable_tuple: dict[tuple[str, i32] | None, str] = {None: "nil", ("a", 1): "pair"}
    print(nullable_tuple, nullable_tuple[None], nullable_tuple[("a", 1)])
    try:
        print(nullable_tuple[("missing", 0)])
    except KeyError as e:
        print(e, repr(e))
    floats: dict[str, f64] = {"x": 1.5}
    print(floats, floats.get("none", -1.0))
    nested_keys: dict[tuple[tuple[str, i64], bool], str] = {(("雪", -10), True): "value"}
    print(nested_keys, nested_keys[(("雪", -10), True)])
    empties: dict[tuple[()], str] = {(): "empty"}
    print(empties, empties[()])
    work: dict[i32, i32] = {}
    for i in range(4):
        work[i] = i
    print(early(work))
    for k in work:
        for k2, v2 in work.items():
            if k2 == 1:
                continue
            if k2 == 2:
                break
            print(k, k2, v2)
    try:
        for k in work:
            raise ValueError("exit iterator")
    except ValueError as e:
        print(e)
    for i in range(200):
        del work[i % 4]
        work[i % 4] = i
    print(work, early(work))
    # Rehashing during a live iteration must retain its cursor, even when a
    # deletion and insertion restore the size before the iterator resumes.
    work = {0: 0, 1: 1, 2: 2, 3: 3}
    for k in work:
        if k == 0:
            del work[3]
            work[4] = 4
        print(k, work[k])


main()
