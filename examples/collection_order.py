from snek import i32


def key(label: str) -> str:
    print("key", label)
    return label


def value(n: i32) -> i32:
    print("value", n)
    return n


def receiver(d: dict[str, i32]) -> dict[str, i32]:
    print("receiver")
    return d


def default(d: dict[str, i32]) -> i32:
    print("default")
    d["a"] = 100
    return -1


def change(d: dict[str, i32]) -> i32:
    d["a"] = 50
    return 9


def tuple_effect() -> tuple[i32, i32]:
    print("tuple evaluated")
    return 1, 2


def main() -> None:
    d = {key("a"): value(1), key("b"): value(2), key("a"): value(3)}
    receiver(d)[key("a")] = value(4)
    receiver(d)[key("a")] += value(5)
    print(d, receiver(d).get(key("a"), default(d)), d)
    print(key("a") in receiver(d))
    print(receiver(d), change(d))
    print((d, 1), change(d))
    print(len(tuple_effect()), bool(tuple_effect()), type(tuple_effect()).__name__)
    print(tuple_effect()[:0], tuple_effect() * 0, tuple_effect() + ("x", "y"))
    print(2 * ("x",), (1,) + ("two",))
    for k in d:
        d[k] += 1
    print(d)
    try:
        for k in d:
            d["new"] = 3
    except RuntimeError as e:
        print(e)
    d = {"a": 1, "b": 2}
    try:
        for k, v in d.items():
            del d[k]
    except RuntimeError as e:
        print(e)
    d = {"a": 1, "b": 2}
    for k in d:
        del d[k]
        break
    print(d)
    d = {"a": 1, "b": 2}
    try:
        for k in d:
            if k == "a":
                del d["a"]
                d["c"] = 3
            print(k)
    except RuntimeError as e:
        print(e)
    d = {"a": 1, "b": 2}
    try:
        for k in d:
            if k == "a":
                del d["b"]
                d["c"] = 3
            print(k)
    except RuntimeError as e:
        print(e)


main()
