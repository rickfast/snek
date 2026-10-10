from snek import i32, f64


def greet(name: str, times: i32) -> str:
    out = ""
    for i in range(times):
        out = out + f"hello {name} #{i}; "
    return out


def mean(xs: list[f64]) -> f64:
    total = 0.0
    for x in xs:
        total += x
    return total / f64(len(xs))


def main() -> None:
    print(greet("wasm", 2))
    print("mean:", mean([1.5, 2.5, 3.5]))
    print(7 // 2, -7 // 2, 7 % -3, -7 % 3)
    print(True, 1 < 2, 10 / 4)


if __name__ == "__main__":
    main()
