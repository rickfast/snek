"""Regression tests for divergences found by an independent review of the exceptions work."""
from snek import i32, f64


class Box:
    v: i32 = 0


class Node:
    v: str = "n"


class Base:
    x: i32

    def __init__(self, x: i32) -> None:
        self.x = x
        print("  Base built", self.x)
        if x > 0:
            return  # leaves the inlined base __init__, not the subclass's
        print("  non-positive")


class Derived(Base):
    tag: str = "d"

    def __init__(self, x: i32) -> None:
        super().__init__(x)
        print("  back in Derived with", self.x)


class Probe(Exception):
    def probe(self) -> str:
        try:
            raise Probe("inner")
        except Probe as self:  # rebinds a parameter
            return str(self)


class Widget:
    def new(self) -> str:  # a method named like the generated constructor
        return "Widget.new"


def __main__() -> str:  # a function named like the generated entry point
    return "user __main__"


def opt_list_names() -> str:  # names that look like monomorphized type names
    return "opt_ and list_ prefixes are fine"


def idx(tag: str, i: i32) -> i32:
    print("  index", tag)
    return i


def val(tag: str, v: i32) -> i32:
    print("  value", tag)
    return v


def box(tag: str, b: Box) -> Box:
    print("  target", tag)
    return b


def grow(xs: list[i32]) -> i32:
    xs.append(9)
    return len(xs)


def handler_param(e: ValueError) -> None:
    try:
        raise ValueError("inner")
    except ValueError as e:
        print("  handler bound to a parameter:", e)


def show_key(k: KeyError) -> None:
    print(f"  [{k}]")


def side(log: list[str]) -> None:
    log.append("ran")


def main() -> None:
    print("str() of a None-valued call still runs it:")
    log: list[str] = []
    print(" ", side(log), log, f"{side(log)}", log)

    print("store order (value first, then target):")
    xs = [1, 2, 3]
    xs[idx("a", 0)] = val("a", 10)
    b = Box()
    box("b", b).v = val("b", 5)
    print(" ", xs[0], b.v)
    try:
        xs[idx("c", 7)] = val("c", 1)
    except IndexError as e:
        print("  caught", e)

    print("print converts at write time:")
    ys = [1]
    print(" ", ys, grow(ys))

    print("KeyError str is repr(key):")
    show_key(KeyError("plain"))
    show_key(KeyError(""))
    show_key(KeyError("it's"))
    show_key(KeyError("tab\there"))
    show_key(KeyError("back\\slash"))
    show_key(KeyError('both \' and "'))
    show_key(KeyError("café \x7f"))
    show_key(KeyError("nbsp  soft­ zw​ ls  bom﻿ tag\U000e0001 cjk中"))
    show_key(KeyError())

    print("lists of strings print with repr:")
    names: list[str | None] = ["a", None, "it's"]
    print(" ", ["x", "y"], names, [["nested"]])

    print("return inside an inlined base __init__:")
    print(" ", Derived(5).x, Derived(-1).tag)

    print("names that used to collide or be rejected:")
    print(" ", Widget().new(), "|", __main__(), "|", opt_list_names())
    handler_param(ValueError("outer"))
    print(" ", Probe("outer").probe())

    n: Node | None = None
    n = Node()
    if n is not None:  # declared optional, currently narrowed: still allowed
        print("  is-not-None after narrowing:", n.v)
    err: ValueError | None = None
    try:
        raise ValueError("x")
    except ValueError as err:  # optional local reused as the handler name
        print("  optional handler name:", err)

    caught: Exception = LookupError("l")
    if isinstance(caught, LookupError) and not isinstance(caught, KeyError):
        print("  isinstance after narrowing ok")

    print("float -> int conversions raise like int():")
    for x in [2.9, -2.9]:
        print("  i32 of", x, "=", i32(x))
    inf = 1e308 * 10.0
    for x in [inf, -inf, inf - inf]:
        try:
            print("  ", i32(x))
        except (OverflowError, ValueError) as e:
            print(" ", type(e).__name__, e)


main()
