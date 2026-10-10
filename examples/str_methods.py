"""The str methods, compared against CPython, including Unicode edge cases."""

from snek import i32, i64


def searching() -> None:
    s = "banana bandana"
    print(s.find("ana"), s.find("ana", 2), s.find("ana", 2, 5), s.find("xyz"), s.find(""), s.find("", 99))
    print(s.rfind("ana"), s.rfind("ana", 0, 6), s.rfind(""), s.index("band"), s.rindex("a"))
    print(s.count("a"), s.count("ana"), s.count(""), s.count("a", 3, -3), "".count(""))
    print(s.startswith("ban"), s.startswith("ana", 1), s.startswith("ana", 1, 3), s.endswith("dana"))
    print(s.endswith("ban", 0, 3), s.startswith(""), "".endswith(""), "".startswith("", 1))
    u = "ünïcödé ünïcödé"
    print(u.find("cöd"), u.rfind("cöd"), u.count("ü"), u.index("é", 7), u.startswith("cö", 3))
    try:
        print(s.index("zz"))
    except ValueError as e:
        print("ValueError:", e)
    try:
        print(s.rindex("a", 20))
    except ValueError as e:
        print("ValueError:", e)


def splitting() -> None:
    print("  a  b\tc\n d  ".split(), "".split(), "   ".split())
    print("a b c d".split(None, 2), "  a b c  ".split(None, 1), "a b".split(maxsplit=0))
    print("a,b,,c,".split(","), "a::b::c".split("::"), "abc".split("x"), "".split(","))
    print("a,b,c".split(",", 1), "a,b,c".rsplit(",", 1), "a,b,c".rsplit(","), "  a b c  ".rsplit(None, 1))
    print("x y z　w".split(), "\x1c\x1d\x1ea\x1fb".split())
    print("one\ntwo\r\nthree\rfour\x0bfive six".splitlines())
    print("keep\nends\r\n".splitlines(True), "".splitlines(), "\n".splitlines(), "no break".splitlines())
    try:
        print("a".split(""))
    except ValueError as e:
        print("ValueError:", e)
    print("-".join(["a", "b", "c"]), "".join([]), ", ".join(["solo"]), "→".join(["α", "β"]))
    words = "the rain in spain".split()
    print("_".join(words).upper())


def stripping() -> None:
    print(repr("  padded \t\n".strip()), repr("　wide ".strip()), repr("xxhixx".strip("x")))
    print(repr("  left".lstrip()), repr("right  ".rstrip()), repr("abcba".strip("ab")), repr("".strip()))
    print(repr("ééaéé".strip("é")), repr("www.example.com".lstrip("w.")), repr("mississippi".rstrip("ip")))
    print("prefix-body".removeprefix("prefix-"), "body".removeprefix("x"), "file.tar.gz".removesuffix(".gz"))
    print(repr("abc".removesuffix("")), repr("abc".removeprefix("abc")))


def replacing() -> None:
    print("aaaa".replace("a", "b"), "aaaa".replace("a", "b", 2), "aaaa".replace("aa", "c"), "abc".replace("x", "y"))
    print("abc".replace("", "-"), "abc".replace("", "-", 2), "".replace("", "x"), "abc".replace("", ""))
    print("héllo".replace("é", "e"), "naïve".replace("ï", "ii"), "a-b-c".replace("-", "", 0))
    print("one two two".replace("two", "2", count=1))


def casing() -> None:
    print("Hello World".upper(), "Hello World".lower(), "hello world".title(), "hELLO".swapcase())
    print("straße".upper(), "ﬁne".upper(), "İstanbul".lower(), len("İ".lower()), "ǆemal".title())
    print("ΟΔΟΣ".lower(), "ΣΑ".lower(), "Σ".lower(), "ΑΣ.".lower(), "ΑΣ'Α".lower(), "Σ́Σ".lower())
    print("they're bill's friends".title(), "hello wORLD".capitalize(), "ǅx".capitalize(), "".capitalize())
    print("Straße".casefold(), "ΣΑΣ".casefold(), "ﬀ".casefold(), "MiXeD 123".swapcase())
    print("ſ".upper(), "K".lower(), "ı".upper(), "ß".title())


def predicates() -> None:
    samples = ["", "abc", "ABC", "Abc", "Hello World", "hello world", "123", "١٢٣", "½", "²", "   ", "\t\n",
               "a1", "ǅ", "_x9", "9x", "über", "Ⅻ", "x​", "😀", "ascii", "Ünï"]
    for s in samples:
        flags = [s.isalpha(), s.isdigit(), s.isdecimal(), s.isnumeric(), s.isalnum(), s.isspace(), s.isupper(),
                 s.islower(), s.istitle(), s.isidentifier(), s.isprintable(), s.isascii()]
        line = ""
        for f in flags:
            line += "1" if f else "0"
        print(repr(s), line)


def padding() -> None:
    print(repr("ab".ljust(5)), repr("ab".rjust(5, "*")), repr("ab".center(7, "-")), repr("ab".center(5)))
    print(repr("abc".center(6)), repr("abc".ljust(2)), repr("é".rjust(3, "→")), repr("x".center(4, "·")))
    print("42".zfill(6), "-42".zfill(6), "+7".zfill(4), "abc".zfill(2), "".zfill(3), "-".zfill(3))
    try:
        print("x".ljust(4, "ab"))
    except TypeError as e:
        print("TypeError:", e)


def parsing() -> None:
    print(i32("42"), i32("  -17\n"), i32("+8"), i32("1_000_000"), i32("007"), i64("-9223372036854775808"))
    print(i32("٤٢"), i32("　 12 　"), i32("-2147483648"), i64("9_223_372_036_854_775_807"))
    for bad in ["", " ", "1.5", "1__0", "_1", "1_", "0x10", "\x1c5", "--1", "+", "12a", "four"]:
        try:
            print(i32(bad))
        except ValueError as e:
            print("ValueError:", e)


def main() -> None:
    searching()
    splitting()
    stripping()
    replacing()
    casing()
    predicates()
    padding()
    parsing()


main()
