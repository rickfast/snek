"""chr() of a computed value: the compiler embeds full Unicode tables for this one."""

from snek import i32


def shift(text: str, k: i32) -> str:
    out: list[str] = []
    for ch in text:
        c = ord(ch)
        if ch.isupper() and ch.isascii():
            out.append(chr((c - 65 + k) % 26 + 65))
        elif ch.islower() and ch.isascii():
            out.append(chr((c - 97 + k) % 26 + 97))
        else:
            out.append(ch)
    return "".join(out)


def describe(cp: i32) -> str:
    ch = chr(cp)
    kinds: list[str] = []
    if ch.isalpha():
        kinds.append("alpha")
    if ch.isdecimal():
        kinds.append("decimal")
    if ch.isspace():
        kinds.append("space")
    if not ch.isprintable():
        kinds.append("nonprint")
    if ch.upper() != ch:
        kinds.append("upper=" + ch.upper())
    return f"U+{cp} {ch!r} {kinds}"


def main() -> None:
    msg = "Attack at dawn, Zürich!"
    secret = shift(msg, 3)
    print(secret)
    print(shift(secret, -3) == msg)
    for cp in [65, 0xDF, 0x3A3, 0x660, 0x2028, 0x1F40D, 0xE000, 0x10FFFF, 0x0345, 0xFB01]:
        print(describe(cp))
    total = 0
    cp = 0x370
    while cp < 0x400:
        if chr(cp).isupper():
            total += 1
        cp += 1
    print("Greek and Coptic uppercase:", total)
    print(chr(0x3A3 + 32).upper(), ("ΣΑΣ" + chr(0x3A3)).lower(), chr(0x130).lower(), len(chr(0x130).lower()))
    try:
        print(chr(0xD800))
    except ValueError:
        print("surrogates are rejected")


main()
