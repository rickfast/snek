"""Strings index by code point, like CPython: len, s[i], slices, iteration, in."""

from snek import i32


def show(label: str, value: str) -> None:
    print(label, repr(value))


def indexing() -> None:
    s = "naïve café 😀!"
    print(len(s), len("😀"), len(""))
    show("first", s[0])
    show("accented", s[2])
    show("emoji", s[-2])
    show("last", s[-1])
    i = 0
    out = ""
    while i < len(s):
        out = out + s[len(s) - 1 - i]
        i += 1
    show("reversed by index", out)
    try:
        print(s[len(s)])
    except IndexError as e:
        print("IndexError:", e)
    try:
        print(""[0])
    except IndexError as e:
        print("IndexError:", e)


def slicing() -> None:
    s = "αβγδε-abc"
    show("[1:4]", s[1:4])
    show("[:3]", s[:3])
    show("[5:]", s[5:])
    show("[-3:]", s[-3:])
    show("[::2]", s[::2])
    show("[::-1]", s[::-1])
    show("[7:1:-2]", s[7:1:-2])
    show("[100:]", s[100:])
    show("[-100:2]", s[-100:2])
    show("[3:3]", s[3:3])
    show("[None:2]", s[None:2])
    ascii_text = "hello, world"
    show("ascii [7:]", ascii_text[7:])
    show("ascii [::-3]", ascii_text[::-3])
    step = 0
    try:
        print(s[::step])
    except ValueError as e:
        print("ValueError:", e)


def iteration() -> None:
    word = "Grüße 🐍"
    n = 0
    for ch in word:
        print(ch, ord(ch), len(ch))
        n += 1
    print(n == len(word))
    letters: list[str] = []
    for ch in "abc":
        letters.append(ch * 2)
    print(letters)


def membership() -> None:
    s = "the quick brown fox"
    print("quick" in s, "slow" in s, "" in s, "fox" not in s)
    print("é" in "café", "e" in "café")
    words = ["apple", "pear"]
    print("pear" in words, "plum" in words)


def characters() -> None:
    print(ord("A"), ord("é"), ord("€"), ord("😀"))
    print(chr(97), chr(0xE9), chr(0x20AC), chr(0x1F600))
    try:
        print(ord("ab"))
    except TypeError as e:
        print("TypeError:", e)
    try:
        print(ord(""))
    except TypeError as e:
        print("TypeError:", e)
    try:
        print(chr(0x110000))
    except ValueError as e:
        print("ValueError:", e)


def operators() -> None:
    print("ab" * 3, 3 * "ab", repr("x" * 0), repr("x" * -2))
    print("é" * 4, len("é" * 4))
    s = "a"
    s += "β"
    s += "c"
    print(s, len(s))
    print("abc" < "abd", "é" > "z", "a" < "aa")
    print(repr("tab\there"), repr("quote'"), repr('both\'"'), repr("\x00\x7f"), repr("​ x"))
    v = "café"
    print(f"{v!r} {v!s} {v}")
    print(str("same"), repr(42), repr(1.5), repr(True), repr([1, 2]), repr(["a", None]))
    print(bool(""), bool("x"))


def main() -> None:
    indexing()
    slicing()
    iteration()
    membership()
    characters()
    operators()


main()
