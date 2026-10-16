from snek import i32, i64, f32


def once(events: list[i32]) -> i32:
    events.append(len(events))
    return len(events)


def main() -> None:
    text = "雪😀abc"
    print(f"[{text:10}][{text:>10s}][{text:🦊^10.3s}][{text:010.2}]")
    print(f"[{'abc':^6s}][{'abc':^06}][{'abcdef':.0s}]")
    print(f"{True} {True:} {True:d} {False:05} {True:10.2f}")
    for v in [0, 1, -1, 42, -42, 65535, -2147483648]:
        print(f"{v:b} {v:#b} {v:#o} {v:#x} {v:#X} {v:+d} {v: d}")
        print(f"[{v:*>15d}][{v:0=15d}][{v:+#016_x}][{v:015,d}][{v:^16_d}]")
        print(f"{v:.2f} {v:.3e} {v:.4g} {v:.1%}")
    for wide in [i64(-9223372036854775808), i64(9223372036854775807)]:
        print(f"{wide} {wide:d} {wide:#x} {wide:#_b} {wide:025,d}")
    print(f"{65:c} {9731:c} {128512:🦊^5c}")
    for v in [-1, 1114112]:
        try:
            print(f"{v:c}")
        except OverflowError as e:
            print(e)
    for value in [0.0, -0.0, 0.00001, 0.0001, 0.001, 1.0, 9.99, 99.9, 999.9,
              2.675, 1.25, 1.75, -0.0001, 1234567.25, 1e20, 1e-20,
              5e-324, 2.2250738585072014e-308, 1.7976931348623157e308,
              1e999, -1e999, 1e999 - 1e999]:
        print(f"{value:.0f}|{value:.2f}|{value:#.0f}|{value:.20f}|{value:+015,.2f}|{value:z.2f}")
        print(f"{value:.0e}|{value:#.0E}|{value:.5e}|{value:.0g}|{value:.3g}|{value:#.3G}")
        print(f"{value:.0}|{value:#.0}|{value:.3}|{value:#.3}|{value:.2%}|{value:+020.3F}")
    print(f"{-0.0:z}|{-0.0:z010,.2f}|{12.0:#}|{1e20:#}")
    small: f32 = 1.25
    print(f"{small:.1f} {small:.3g}")
    print(f"{'a\n'!r:>12} {123!s:*^7.2} {[1, 2]!r:>12} {None!s:>6}")
    events: list[i32] = []
    print(f"{once(events):03d} {once(events):03d}", events)


main()
