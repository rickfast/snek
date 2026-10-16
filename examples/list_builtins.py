from snek import i32, i64, f32, f64


def reverse_and_mutate(xs: list[i32]) -> bool:
    xs.append(0)
    return True


def default_and_mutate(xs: list[i32]) -> i32:
    xs.append(-10)
    return 99


def choose(values: list[i32], events: list[i32]) -> list[i32]:
    events.append(1)
    return values


def start(values: list[i32], events: list[i32]) -> i32:
    events.append(2)
    values.append(10)
    return 3


def fallback(flag: bool) -> str | None:
    return "fallback" if flag else None


def main() -> None:
    xs = [3, 1, 4, 1, 5, -9, 2, 6]
    print(sum(xs), sum(xs, 10), sum(xs, start=i64(10)), sum(xs, 0.5))
    print(min(xs), max(xs), min(xs, default=100), max(xs, default=-100, key=None))
    print(xs, sorted(xs), sorted(xs, reverse=True), sorted(xs, key=None))
    empty: list[i32] = []
    print(sum(empty), sum([], 9), min(empty, default=7), max(empty, default=-7), sorted(empty))
    copied = sorted(xs)
    copied.append(100)
    print(xs, copied, sum([]), min([], default=4), max([], default="empty"))
    empty_sorted: list[i32] = sorted([])
    print(empty_sorted)
    for flag in [False, True]:
        try:
            if flag:
                print(min(empty))
            else:
                print(max(empty))
        except ValueError as e:
            print(e)
    words = ["雪", "a", "😀", "é", "", "a", "é"]
    print(sorted(words), sorted(words, reverse=True), min(words), max(words))
    no_words: list[str] = []
    print(min(no_words, default=None), max(words, default=None))
    print(min(no_words, default=fallback(True)), max(words, default=fallback(False)))
    flags = [True, False, True, True]
    print(sum(flags), sum(flags, i64(10)), min(flags), max(flags), sorted(flags), sorted(flags, reverse=True))
    wide: list[i64] = [9007199254740993, -9223372036854775808, 9223372036854775807, 0]
    print(sorted(wide), min(wide), max(wide))
    floats = [1e16, 1.0, -1e16]
    print(sum(floats, 0.0), sum([1e16, 1.0, -1e16]), sum([0.1, 0.1, 0.1], start=0.0))
    print(sum([1.0, 2.5], 3), sum([1.0], 1 if len(xs) > 0 else 2.0))
    no_floats: list[f64] = []
    print(sum(no_floats, -0.0))
    signed = [-0.0, 0.0, -0.0, 0.0]
    print(sorted(signed), sorted(signed, reverse=True), min(signed), max(signed))
    print(min(0.0, -0.0), max(-0.0, 0.0))
    nan = 1e999 - 1e999
    print(min([nan, 1.0]), max([nan, 1.0]), min([1.0, nan]), max([1.0, nan]))
    print(min(nan, 1.0), max(1.0, nan), sum([1e999, 1.0], 0.0), sum([1e999, -1e999], 0.0))
    small: list[f32] = [0.25, -0.5, 0.75]
    print(sorted(small), sum(small, 0.0), min(small), max(small))
    xs = [3, 2, 1]
    print(sorted(xs, reverse=reverse_and_mutate(xs)), xs)
    print(min(xs, default=default_and_mutate(xs)), xs)
    events: list[i32] = []
    print(sum(choose(xs, events), start=start(xs, events)), xs, events)
    many: list[i32] = []
    for i in range(513):
        many.append((i * 71) % 100)
    print(sorted(many), sorted(many, reverse=True), sum(many))


main()
