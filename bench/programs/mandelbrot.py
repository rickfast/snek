"""Tight floating-point loops."""
from snek import i32, f64


def mandel(size: i32, max_iter: i32) -> i32:
    inside = 0
    for y in range(size):
        ci = 2.0 * f64(y) / f64(size) - 1.0
        for x in range(size):
            cr = 2.0 * f64(x) / f64(size) - 1.5
            zr = 0.0
            zi = 0.0
            i = 0
            while i < max_iter and zr * zr + zi * zi <= 4.0:
                t = zr * zr - zi * zi + cr
                zi = 2.0 * zr * zi + ci
                zr = t
                i += 1
            if i == max_iter:
                inside += 1
    return inside


def main() -> None:
    print(mandel(200, 100))


if __name__ == "__main__":
    main()
