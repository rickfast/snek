"""Regenerate the parser's pinned Unicode 15.1 character-name data (offline).

Requires a Python whose unicodedata.unidata_version is exactly 15.1.0. Normal
compiler builds do not run this generator or import unicodedata for name lookup.
The vendored NameAliases input is hash-checked before use. --check is read-only;
--write replaces only the marked generated block in parser.py.
"""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import unicodedata

ROOT = Path(__file__).resolve().parent
VERSION = "15.1.0"
ALIASES_SHA256 = "fbf0e640bab36e165c4da5b6a98bdd963fcb4f923b5097f26f6f7f18b9678698"
BEGIN = "# BEGIN GENERATED UNICODE NAMES\n"
END = "# END GENERATED UNICODE NAMES\n"


def aliases() -> dict[str, int]:
    data = (ROOT / "data/NameAliases-15.1.0.txt").read_bytes()
    if hashlib.sha256(data).hexdigest() != ALIASES_SHA256:
        raise ValueError("NameAliases input hash does not match the pinned Unicode 15.1 data")
    result = {}
    for line in data.decode("utf-8").splitlines():
        if line and not line.startswith("#"):
            code, name, _ = line.split(";")
            result[name] = int(code, 16)
    return result


def generate() -> str:
    if unicodedata.unidata_version != VERSION:
        raise ValueError(f"regeneration needs Unicode {VERSION}, got {unicodedata.unidata_version}")
    names = aliases()
    ranges = []
    for code in range(0x110000):
        name = unicodedata.name(chr(code), "")
        if name.startswith("CJK UNIFIED IDEOGRAPH-"):
            if ranges and ranges[-1][1] + 1 == code:
                ranges[-1][1] = code
            else:
                ranges.append([code, code])
        elif name and not name.startswith("HANGUL SYLLABLE "):
            if name in names and names[name] != code:
                raise ValueError("Unicode name collision: " + name)
            names[name] = code
    data = "".join(f"{name}={code:X}\n" for name, code in sorted(names.items()))
    assert data.isascii() and '"' not in data and "\\" not in data
    conditions = [f"(code >= {first} and code <= {last})" for first, last in ranges]
    return (BEGIN
            + f"# Unicode {VERSION}; {len(names)} explicit names/aliases; CJK and Hangul are algorithmic.\n"
            + "# Derived from CPython's pinned UCD and data/NameAliases-15.1.0.txt.\n"
            + "# Unicode data license: data/UNICODE-LICENSE.txt. Do not edit by hand.\n"
            + f"# Table SHA-256: {hashlib.sha256(data.encode()).hexdigest()}\n"
            + "def unicode_ideograph(code: i32) -> bool:\n"
            + "    return (" + "\n            or ".join(conditions) + ")\n\n\n"
            + 'def unicode_name_table() -> str:\n    return r"""' + data + '"""\n'
            + END)


def main() -> None:
    cli = argparse.ArgumentParser(description=__doc__)
    mode = cli.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--write", action="store_true")
    args = cli.parse_args()
    path = ROOT / "parser.py"
    source = path.read_text()
    before, remainder = source.split(BEGIN, 1)
    old, after = remainder.split(END, 1)
    generated = generate()
    if args.write:
        # This file is generated source; preserve all hand-written parser code.
        path.write_text(before + generated + after)
    elif BEGIN + old + END != generated:
        raise SystemExit("Unicode name data is stale; run with --write")
    print("Unicode 15.1 name data verified")


if __name__ == "__main__":
    main()
