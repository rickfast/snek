from snek import i32
from typing import Iterator


class Holder:
    g: Iterator[i32] | None = None


def generate(h: Holder) -> Iterator[i32]:
    for i in range(3):
        g = h.g
        if g is not None:
            try:
                if i == 0:
                    next(g)
                elif i == 1:
                    g.close()
                else:
                    g.throw(ValueError("inside"))
            except ValueError as e:
                print(str(e))
        yield i


def main() -> None:
    h = Holder()
    g = generate(h)
    h.g = g
    print(list(g))
    print(next(g, -1))


main()
