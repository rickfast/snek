"""UTF-8 strings: literals are pooled data segments, concat/compare in the runtime."""
from snek import i32, f64


class Person:
    name: str
    nickname: str | None = None

    def __init__(self, name: str) -> None:
        self.name = name

    def label(self) -> str:
        nick = self.nickname
        if nick is None:
            return self.name
        return f"{self.name} ({nick})"


def repeat(s: str, n: i32) -> str:
    out = ""
    for _ in range(n):
        out += s
    return out


def main() -> None:
    a = Person("Ada")
    b = Person("Grace")
    b.nickname = "Amazing Grace"
    print(a.label(), "|", b.label())
    print(repeat("ab", 3), len(repeat("xyz", 4)))
    print("apple" < "banana", "b" > "abc", "same" == "sa" + "me", "x" != "x")
    words = ["pear", "fig", "kiwi"]
    longest = ""
    for w in words:
        if len(w) > len(longest):
            longest = w
    print("longest:", longest)
    score: f64 = 9.5
    print(f"{a.name} scored {score} in {len(words)} rounds; passed={score > 5.0}")


main()
