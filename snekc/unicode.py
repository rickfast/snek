"""Per-program Unicode tables, generated from the running CPython.

A snek program reads no input, so every code point its strings can hold comes
from its own string literals, unless it calls chr() on a computed value. The
compiler therefore embeds the Unicode properties of just those code points
(closed under the case mappings the program uses), which for most programs is
a handful of table entries. A program that calls chr(n) on a computed n gets
tables covering all of Unicode instead.

Every property is read from CPython's own str methods, so the compiled
program agrees with the CPython that compiled it (and runs the differential
tests), whatever its Unicode version.

Tables cover code points >= 128; ASCII has its own direct-lookup table.
  flags   sorted (start, flags) runs: the flags of cp are those of the last run
          starting at or before it. Gaps between the code points a program can
          hold are don't-cares, so neighbouring runs merge freely.
  cases   per mapping kind, sorted (start, end, stride, delta) runs: cp in
          [start, end) with (cp - start) % stride == 0 maps to cp + delta.
          stride 0 marks a mapping to several code points: delta is then
          (offset << 2) | count into the special code point list.
  zeros   code points whose decimal value is 0 (each starts a run of ten)
"""

from __future__ import annotations

import functools
import re
import unicodedata
from dataclasses import dataclass, field

NONPRINT, SPACE, ALPHA, DECIMAL, DIGIT, NUMERIC = 1, 2, 4, 8, 16, 32
UPPER, LOWER, TITLE, XID_START, XID_CONTINUE, CASE_IGNORABLE = 64, 128, 256, 512, 1024, 2048
CASED = UPPER | LOWER | TITLE  # Unicode's Cased property is exactly Uppercase + Lowercase + Lt
ALL_FLAGS = 4095

FLAG_NAMES = {
    "NONPRINT": NONPRINT, "SPACE": SPACE, "ALPHA": ALPHA, "DECIMAL": DECIMAL, "DIGIT": DIGIT,
    "NUMERIC": NUMERIC, "UPPER": UPPER, "LOWER": LOWER, "TITLE": TITLE, "XID_START": XID_START,
    "XID_CONTINUE": XID_CONTINUE, "CASE_IGNORABLE": CASE_IGNORABLE, "CASED": CASED,
}

UPPER_K, LOWER_K, TITLE_K, FOLD_K = 0, 1, 2, 3
CASE_KINDS = (UPPER_K, LOWER_K, TITLE_K, FOLD_K)
MAX_CP = 0x110000
CAPITAL_SIGMA, FINAL_SIGMA = 0x3A3, 0x3C2


def _case_ignorable(ch: str) -> bool:
    # Read the property off CPython's final-sigma rule (str.lower), which is the only place
    # CPython uses it: an ignorable character between a cased letter and a capital sigma is
    # skipped. '1' is not cased, 'A' is; neither is case-ignorable.
    if ch.isupper() or ch.islower() or unicodedata.category(ch) == "Lt":
        return ("1" + ch + "Σ").lower()[-1] == "σ"
    return ("A" + ch + "Σ").lower()[-1] == "ς"


_BIT_TESTS = {
    NONPRINT: lambda ch: not ch.isprintable(),
    SPACE: str.isspace,
    ALPHA: str.isalpha,
    DECIMAL: str.isdecimal,
    DIGIT: str.isdigit,
    NUMERIC: str.isnumeric,
    UPPER: str.isupper,
    LOWER: str.islower,
    TITLE: lambda ch: unicodedata.category(ch) == "Lt",
    XID_START: str.isidentifier,  # true for '_' too, which isidentifier() accepts first anyway
    XID_CONTINUE: lambda ch: ("a" + ch).isidentifier(),
    CASE_IGNORABLE: _case_ignorable,
}


def flags_of(cp: int, mask: int = ALL_FLAGS) -> int:
    ch = chr(cp)
    out = 0
    for bit, test in _BIT_TESTS.items():
        if mask & bit and test(ch):
            out |= bit
    return out


@functools.lru_cache(maxsize=None)
def _bit_column(bit: int) -> bytes:
    """One property for every code point (only computed for programs using chr() dynamically)."""
    test = _BIT_TESTS[bit]
    return bytes(1 if test(chr(c)) else 0 for c in range(MAX_CP))


def mapping(cp: int, kind: int) -> str:
    ch = chr(cp)
    return (ch.upper, ch.lower, ch.title, ch.casefold)[kind]()


@functools.lru_cache(maxsize=None)
def _full_mapping(kind: int) -> tuple[str, ...]:
    return tuple(mapping(c, kind) for c in range(MAX_CP))


@functools.lru_cache(maxsize=None)
def _all_zeros() -> tuple[int, ...]:
    return tuple(c for c in range(128, MAX_CP) if unicodedata.decimal(chr(c), -1) == 0)


@dataclass
class UnicodeUse:
    """What a program needs, collected by the checker."""

    flags: int = NONPRINT  # repr() can always need printability
    cases: set[int] = field(default_factory=set)
    decimal: bool = False  # Unicode decimal digit values (i32(str))
    dynamic: bool = False  # chr() of a computed value: any code point can occur
    extra: set[int] = field(default_factory=set)  # code points from chr(<literal>)


@dataclass
class Tables:
    ascii_flags: list[int]
    flag_runs: list[tuple[int, int]]
    case_runs: dict[int, list[tuple[int, int, int, int]]]
    specials: list[int]
    zeros: list[int]


def code_points(literals: list[str], use: UnicodeUse) -> set[int] | None:
    """Non-ASCII code points the program can hold, or None for all of them."""
    if use.dynamic:
        return None
    cps = {ord(ch) for text in literals for ch in text if ord(ch) >= 128} | {c for c in use.extra if c >= 128}
    while True:
        more = set()
        for c in cps:
            for k in use.cases:
                more |= {ord(ch) for ch in mapping(c, k) if ord(ch) >= 128}
        if LOWER_K in use.cases and CAPITAL_SIGMA in cps:
            more.add(FINAL_SIGMA)
        if more <= cps:
            return cps
        cps |= more


def build(literals: list[str], use: UnicodeUse) -> Tables:
    mask = use.flags | (DECIMAL if use.decimal else 0)
    cps = code_points(literals, use)
    ascii_flags = [flags_of(c, mask) for c in range(128)]

    # flags
    runs: list[tuple[int, int]] = []
    if cps is None:
        columns = [(bit, _bit_column(bit)) for bit in _BIT_TESTS if mask & bit]
        starts = {128}
        for _, col in columns:  # where any property changes value (found by re, in C)
            starts.update(m.start() for m in re.finditer(rb"\x00+|\x01+", col) if m.start() > 128)
        for c in sorted(starts):
            f = sum(bit for bit, col in columns if col[c])
            if not runs or runs[-1][1] != f:
                runs.append((c, f))
    else:
        for c in sorted(cps):
            f = flags_of(c, mask)
            if not runs or runs[-1][1] != f:
                runs.append((c, f))

    # case mappings
    specials: list[int] = []
    case_runs: dict[int, list[tuple[int, int, int, int]]] = {}
    for k in CASE_KINDS:
        out: list[tuple[int, int, int, int]] = []
        if k in use.cases:
            source = range(128, MAX_CP) if cps is None else sorted(cps)
            full = _full_mapping(k) if cps is None else None
            for c in source:
                m = full[c] if full is not None else mapping(c, k)
                if len(m) == 1 and ord(m) == c:
                    continue
                if len(m) > 1:
                    out.append((c, c + 1, 0, (len(specials) << 2) | len(m)))
                    specials.extend(ord(x) for x in m)
                    continue
                delta = ord(m) - c
                if out:
                    s, e, stride, d = out[-1]
                    # extend a run of equal deltas, every code point or every other one
                    if stride and d == delta:
                        if stride == 1 and c == e:
                            out[-1] = (s, c + 1, 1, d)
                            continue
                        if e - s == 1 and c == s + 2:
                            out[-1] = (s, c + 1, 2, d)
                            continue
                        if stride == 2 and c == e + 1:
                            out[-1] = (s, c + 1, 2, d)
                            continue
                out.append((c, c + 1, 1, delta))
        case_runs[k] = out

    zeros: list[int] = []
    if use.decimal:
        if cps is None:
            zeros = list(_all_zeros())
        else:
            zeros = sorted({c - unicodedata.decimal(chr(c)) for c in cps if unicodedata.decimal(chr(c), -1) >= 0})
    return Tables(ascii_flags, runs, case_runs, specials, zeros)
