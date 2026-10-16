"""Named Unicode escapes, aliases, algorithmic names, raw strings and f-strings."""
from snek import i32


def main() -> None:
    value: i32 = 7
    SNOWMAN: i32 = 12
    print("\N{SNOWMAN}\N{LATIN SMALL LETTER E WITH ACUTE}\N{GRINNING FACE}")
    print("\N{CJK UNIFIED IDEOGRAPH-4E00}\N{HANGUL SYLLABLE GGWAEGS}")
    print(ord("\N{NUL}"), ord("\N{BOM}"), ord("\N{VS256}"))
    print("\N{LATIN CAPITAL LETTER GHA}" == "\N{LATIN CAPITAL LETTER OI}")
    print(f"\N{SNOWMAN}{value}\N{LEFT CURLY BRACKET}\N{RIGHT CURLY BRACKET}")
    print(f"[{value:\N{SPACE}>4}] [{value:\N{snowman}>4}]")
    print(r"\N{SNOWMAN}", rf"\N{SNOWMAN}")


if __name__ == "__main__":
    main()
