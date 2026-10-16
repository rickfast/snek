from snek import i32, i64, f32, f64
from typing import Callable


class Player:
    name: str
    score: i32

    def __init__(self, name: str, score: i32) -> None:
        self.name = name
        self.score = score


def negative(n: i32) -> i32:
    return -n


def score(player: Player) -> i32:
    return player.score


def wide(n: i32) -> i64:
    return i64(n) * i64(10000000000)


def single(n: i32) -> f32:
    return f32(n) * f32(0.5)


def main() -> None:
    xs = [3, -1, 4, 1, -5, 2, -2, 0]
    print(sorted(xs, key=negative), min(xs, key=negative), max(xs, key=negative))
    print(sorted(xs, key=lambda x: abs(x)), sorted(xs, reverse=True, key=lambda x: abs(x)))
    print(min(xs, key=lambda x: abs(x)), max(xs, key=lambda x: abs(x)), xs)
    print(sorted(xs, key=lambda x: x < 0), sorted(xs, key=wide), sorted(xs, key=single))
    print(min(xs, key=wide), max(xs, key=single), sorted(xs, key=lambda x: f64(x) * 0.5))
    print(sorted([True, False, True], key=lambda x: not x))
    print(sorted([i64(9), i64(-2), i64(1)], key=lambda x: -x))
    print(sorted([0.25, -0.5, 0.75], key=lambda x: -x))
    players = [Player("first", 4), Player("second", 1), Player("third", 4), Player("fourth", 2)]
    for p in sorted(players, key=lambda p: p.score):
        print(p.name)
    for p in sorted(players, key=score, reverse=True):
        print(p.name)
    print(max(players, key=score) is players[0], min(players, key=score) is players[1])
    print(max(players[0], players[2], key=lambda p: p.score).name)
    print(min(-3, 2, key=lambda x: abs(x)), max(-3, 2, key=lambda x: abs(x)))
    print(min(3, 2, key=None))
    words = ["snow", "雪", "😀", "a", "", "é"]
    print(sorted(words, key=lambda w: len(w)), min(words, key=lambda w: len(w)), max(words, key=lambda w: len(w)))
    print(sorted(xs, key=lambda x: str(x)))
    rows: list[tuple[str, i32]] = [("a", 2), ("b", 1), ("c", 2)]
    print(sorted(rows, key=lambda row: row[1]), max(rows, key=lambda row: row[1]))
    print(sorted(rows, key=lambda row: row[0], reverse=True))
    empty: list[Player] = []
    best = max(empty, key=score, default=None)
    print(best is None, min(empty, default=players[0], key=score) is players[0])
    print(sorted([], key=negative), min([], key=negative, default=99))
    try:
        min(empty, key=score)
    except ValueError as e:
        print(e)
    try:
        max(empty, key=lambda p: p.score)
    except ValueError as e:
        print(e)
    offset = 5
    print(sorted(xs, key=lambda x: abs(x - offset)))
    key: Callable[[i32], i32] = negative
    print(sorted(xs, key=key))
    optional: list[str | None] = ["ab", None, "c"]
    print(sorted(optional, key=lambda s: len(s) if s is not None else 0))
    print(min(optional, key=lambda s: len(s) if s is not None else 0))
    print(max(optional, key=lambda s: len(s) if s is not None else 0, default=None))
    functions: list[Callable[[i32], i32]] = [negative, key]
    best_function = max(functions, key=lambda f: f(3), default=lambda n: n)
    print(best_function(3))
    functions.clear()
    print(min(functions, key=lambda f: f(3), default=lambda n: n + 1)(3))


main()
