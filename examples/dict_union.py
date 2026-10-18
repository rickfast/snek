from snek import i32, i64


class Box:
    values: dict[str, i32]

    def __init__(self, values: dict[str, i32]) -> None:
        self.values = values


def changed(values: dict[str, i32], label: str) -> dict[str, i32]:
    print(label)
    values["changed"] = 7
    return {"right": 4, "first": 9}


def main() -> None:
    left = {"first": 1, "second": 2}
    right = {"second": 20, "third": 3}
    merged = left | right
    print(left, right, merged)
    print(merged is left, merged is right)
    print(list(merged), {} | left, left | {})
    left |= right
    print(left, left is merged)
    left |= left
    print(left)
    del left["second"]
    left |= {"second": 22}
    print(list(left), left)

    nested = {"shared": [1]}
    copy = nested | {}
    copy["shared"].append(2)
    print(nested, copy)

    box = Box({"first": 1})
    alias = box.values
    box.values |= changed(box.values, "field rhs")
    print(box.values, alias is box.values)
    slots = [{"first": 1}]
    alias = slots[0]
    slots[0] |= changed(slots[0], "index rhs")
    print(slots, alias is slots[0])
    outer = {"slot": {"first": 1}}
    alias = outer["slot"]
    outer["slot"] |= changed(outer["slot"], "dict rhs")
    print(outer, alias is outer["slot"])

    original = {"first": 1}
    print(original | changed(original, "union rhs"))
    print(original)
    value = {"old": 1}
    saved = value

    def rebind() -> dict[str, i32]:
        nonlocal value
        value = {"replacement": 2}
        return {"new": 3}

    value |= rebind()
    print(value, value is saved)
    wide: dict[i64, i64] = {1: 2}
    print(wide | {2: 3})
    wide |= {3: 4}
    print(wide)
    keys: dict[tuple[str, i32], str] = {("a", 1): "old"}
    print(keys | {("a", 1): "new", ("b", 2): "second"})
    nullable: dict[str | None, str | None] = {None: None}
    print(nullable | {None: "present", "missing": None})
    many: dict[i32, i32] = {}
    for index in range(100):
        many[index] = index
    many |= {100: 100, 0: -1}
    print(len(many), many[0], many[100])
    try:
        for key in many:
            many |= {101: 101}
    except RuntimeError as error:
        print(str(error))


main()
