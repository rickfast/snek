from snek import i32, i64, checkpoint, run


def changed(base: dict[str, i32]) -> dict[str, i32]:
    print("unpack rhs")
    base["original"] = 99
    return {"added": 2}


def key() -> str:
    print("key")
    return "original"


def value(base: dict[str, i32]) -> i32:
    print("value")
    base["original"] = 100
    return 3


async def delayed(base: dict[str, i32]) -> i32:
    await checkpoint()
    base["original"] = 200
    return 4


async def suspended(base: dict[str, i32]) -> dict[str, i32]:
    return {**base, "awaited": await delayed(base)}


async def main() -> None:
    base = {"original": 1, "second": 2}
    copy = {**base}
    print(copy, copy is base)
    print({"prefix": 0, **base, "original": 5, **{"suffix": 6}})
    print({**base, **changed(base)}, base)
    print({**base, key(): value(base), "tail": 7}, base)
    print(await suspended(base), base)
    nested = {"items": [1]}
    shallow = {**nested}
    shallow["items"].append(2)
    print(nested, shallow)
    empty: dict[str, i32] = {**{}}
    print(empty)
    contextual: dict[str, i32] = {**{}, **base}
    print(contextual)
    wide: dict[i64, i64] = {1: 2}
    print({**wide, 3: 4})
    nullable: dict[str | None, str | None] = {None: None}
    print({**nullable, "key": None, None: "present"})
    keys = {("a", 1): "first"}
    print({**keys, ("a", 1): "last", ("b", 2): "second"})
    many: dict[i32, i32] = {}
    for index in range(100):
        many[index] = index
    merged = {**many, 100: 100, 0: -1, **many}
    print(len(merged), merged[0], merged[100])


run(main())
