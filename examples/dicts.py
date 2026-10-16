from snek import i32, i64


def main() -> None:
    d = {"first": 1, "second": 2, "first": 3}
    print(d, len(d), bool(d), type(d).__name__)
    print(d["first"], "second" in d, "missing" not in d)
    d["third"] = 4
    d["second"] += 10
    print(d, d.get("absent", -1), d.setdefault("first", 50), d.setdefault("last", 5))
    keys, values, items = d.keys(), d.values(), d.items()
    print(keys, values, items)
    del d["second"]
    d["second"] = 20
    print(keys, values, items)
    print(list(d), list(keys), list(values), list(items))
    print("first" in keys, 20 in values, ("first", 3) in items, ("first", 4) not in items)
    for k in d:
        print(k, d[k])
    for k, v in items:
        print(k, v)
    for v in values:
        print(v, end=" ")
    print()
    copied = d.copy()
    copied.update({"first": 99, "new": 6})
    copied.update(copied)
    print(d, copied, d == dict(d), d != copied)
    print(copied.pop("first"), copied.pop("absent", -5), copied.popitem(), copied)
    empty: dict[str, i32] = dict()
    print(empty, bool(empty), empty == {}, len(empty.keys()))
    for key in empty:
        print("unreachable", key)
    try:
        empty.popitem()
    except KeyError as e:
        print(e, repr(e))
    for key in ["missing", "", "quoted'key", "雪"]:
        try:
            print(d[key])
        except KeyError as e:
            print(type(e).__name__, str(e), repr(e))
        try:
            del d[key]
        except LookupError as e:
            print(e)
    ints: dict[i64, str] = {1: "one", -9223372036854775808: "min", 9223372036854775807: "max"}
    print(ints, ints[1], ints.get(8), ints.get(9, None), ints.pop(9, None))
    try:
        print(ints[12345])
    except KeyError as e:
        print(e, repr(e))
    optional: dict[str, str | None] = {"nil": None, "text": "value"}
    print(optional, optional.get("nil"), optional.get("absent"), optional.setdefault("other"))
    tuple_keys: dict[tuple[str, i32], i32] = {("a", 1): 10, ("b", 2): 20}
    tuple_keys[("a", 1)] += 5
    print(tuple_keys, tuple_keys[("a", 1)])
    try:
        print(tuple_keys[("missing", 0)])
    except KeyError as e:
        print(e, repr(e))
    nullable_keys: dict[str | None, i32] = {None: 0, "key": 1}
    print(nullable_keys, nullable_keys[None])
    flags: dict[bool, str] = {True: "yes", False: "no"}
    print(flags, flags[True])
    nested: dict[str, list[tuple[i32, str]]] = {"rows": [(1, "one")]}
    nested["rows"] += [(2, "two")]
    print(nested, nested == {"rows": [(1, "one"), (2, "two")]})
    print([d, copied], (d, copied))
    d.clear()
    print(d, keys, values, items)


main()
