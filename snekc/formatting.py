"""Parse static format specs, and provide format helpers written in snek.

Explicit float precision uses exact decimal digits of the binary value, then
rounds ties to even. It does not round an already-rounded str(float).
"""

import re
from dataclasses import dataclass

from .errors import CompileError
from .types import STR, Prim


@dataclass(frozen=True)
class FormatSpec:
    kind: str
    precision: int
    alternate: bool
    normalize_zero: bool
    sign: str
    width: int
    align: str
    fill: str
    group: str


_SPEC = re.compile(
    r"(?:(?P<fill>.)?(?P<align>[<>=^]))?(?P<sign>[+\- ])?(?P<z>z)?(?P<alt>\#)?"
    r"(?P<zero>0)?(?P<width>[0-9]+)?(?P<group>[_,])?(?:\.(?P<precision>[0-9]+))?"
    r"(?P<kind>[bcdeEfFgGnosxX%])?", re.DOTALL)


def parse_spec(text: str, ty, node) -> FormatSpec:
    match = _SPEC.fullmatch(text)
    if match is None:
        raise CompileError(f"invalid or unsupported format specifier {text!r}", node)
    sizes = [value.lstrip('0') or '0' for value in (match['width'] or '0', match['precision'] or '0')]
    if any(len(value) > 10 for value in sizes):
        raise CompileError("format width and precision must fit in i32", node)
    width = int(sizes[0])
    precision = int(sizes[1]) if match['precision'] is not None else -1
    if width > 2147483647 or precision > 2147483647:
        raise CompileError("format width and precision must fit in i32", node)
    if match['kind'] == 'n':
        raise CompileError("locale-aware 'n' formatting is not supported; use d or g", node)
    # CPython validates combinations of flags and type codes. Substitute small
    # sizes so validation never allocates an output proportional to the spec.
    probe = text
    for field in ('precision', 'width'):
        a, b = match.span(field)
        if a >= 0:
            probe = probe[:a] + '1' + probe[b:]
    sample = '' if ty == STR else 0.0 if isinstance(ty, Prim) and ty.is_float else 0
    try:
        format(sample, probe)
    except (ValueError, OverflowError) as error:
        raise CompileError(f"invalid format specifier for {ty}: {error}", node) from None
    fill = match['fill'] or ('0' if match['zero'] else ' ')
    align = match['align'] or ('<' if ty == STR else '=' if match['zero'] else '>')
    return FormatSpec(match['kind'] or '', precision, bool(match['alt']), bool(match['z']),
                      match['sign'] or '-', width, align, fill, match['group'] or '')


FORMAT_SOURCE = '''
def __fmt_align(s: str, width: i32, align: i32, fill: str, prefix: i32) -> str:
    margin = width - len(s)
    if margin <= 0:
        return s
    if align == 60:
        return s + fill * margin
    if align == 61:
        return s[:prefix] + fill * margin + s[prefix:]
    if align == 94:
        left = margin // 2
        return fill * left + s + fill * (margin - left)
    return fill * margin + s


def __fmt_text(s: str, precision: i32, width: i32, align: i32, fill: str) -> str:
    if precision >= 0:
        s = s[:precision]
    return __fmt_align(s, width, align, fill, 0)


def __fmt_number(prefix: str, digits: str, tail: str, width: i32, align: i32,
                 fill: str, group: str, size: i32) -> str:
    if len(group) > 0:
        if align == 61 and fill == "0":
            want = width - len(prefix) - len(tail)
            n = len(digits)
            # Include separators in zero padding; the result may exceed width
            # when the next separator makes another leading zero necessary.
            while n + (n - 1) // size < want:
                n += 1
            digits = "0" * (n - len(digits)) + digits
        b = __buf(__blen(digits) * 2)
        for i in range(len(digits)):
            if i > 0 and (len(digits) - i) % size == 0:
                __puts(b, group)
            __put(b, __byte(digits, i))
        digits = __done(b)
    return __fmt_align(prefix + digits + tail, width, align, fill, len(prefix))


def __fmt_char(v: i64, width: i32, align: i32, fill: str) -> str:
    if v < 0 or v > 1114111:
        raise OverflowError("%c arg not in range(0x110000)")
    return __fmt_align(chr(i32(v)), width, align, fill, 0)


def __fmt_int(v: i64, kind: i32, alternate: bool, sign: i32,
              width: i32, align: i32, fill: str, group: str) -> str:
    base: i64 = 10
    size = 3
    prefix = ""
    alphabet = "0123456789abcdef"
    if kind == 98:
        base = 2
        prefix = "0b" if alternate else ""
        size = 4
    elif kind == 111:
        base = 8
        prefix = "0o" if alternate else ""
        size = 4
    elif kind == 120 or kind == 88:
        base = 16
        prefix = "0x" if alternate else ""
        size = 4
        if kind == 88:
            alphabet = "0123456789ABCDEF"
            prefix = "0X" if alternate else ""
    if v < 0:
        prefix = "-" + prefix
    elif sign == 43:
        prefix = "+" + prefix
    elif sign == 32:
        prefix = " " + prefix
    # Accumulate negatively, so INT64_MIN never needs a positive i64.
    n = -v if v > 0 else v
    digits = ""
    while n != 0:
        digit = -(n % -base)
        digits = alphabet[i32(digit)] + digits
        n = (n + digit) // base
    if len(digits) == 0:
        digits = "0"
    return __fmt_number(prefix, digits, "", width, align, fill, group, size)


def __fmt_exact(v: f64) -> tuple[str, i32]:
    # abs(v) = mantissa * 2**exponent. For negative exponents, multiplying by
    # 5**(-exponent) gives an exact decimal integer with a known decimal point.
    bits = __f64_bits(v)
    mantissa = bits & 4503599627370495
    exponent = i32((bits >> 52) & 2047)
    if exponent == 0:
        exponent = -1074
    else:
        mantissa += 4503599627370496
        exponent -= 1075
    if mantissa == 0:
        return "0", 1
    while exponent < 0 and mantissa % 2 == 0:
        mantissa //= 2
        exponent += 1
    chunks: list[i32] = []
    while mantissa > 0:
        chunks.append(i32(mantissa % 1000000000))
        mantissa //= 1000000000
    factor: i64 = 2 if exponent >= 0 else 5
    for step in range(abs(exponent)):
        carry: i64 = 0
        for i in range(len(chunks)):
            product = i64(chunks[i]) * factor + carry
            chunks[i] = i32(product % 1000000000)
            carry = product // 1000000000
        if carry != 0:
            chunks.append(i32(carry))
    b = __buf(len(chunks) * 9)
    __puts(b, str(chunks[-1]))
    for i in range(len(chunks) - 2, -1, -1):
        __puts(b, str(chunks[i]).zfill(9))
    digits = __done(b)
    return digits, len(digits) + min(exponent, 0)


def __fmt_round(digits: str, keep: i32) -> str:
    if keep < 0:
        return "0"
    if keep >= len(digits):
        return digits + "0" * (keep - len(digits))
    next_digit = __byte(digits, keep) - 48
    up = next_digit > 5
    if next_digit == 5:
        up = keep > 0 and (__byte(digits, keep - 1) & 1) != 0
        for i in range(keep + 1, len(digits)):
            if __byte(digits, i) != 48:
                up = True
    result = digits[:keep]
    if up:
        end = keep
        while end > 0 and __byte(result, end - 1) == 57:
            end -= 1
        if end == 0:
            return "1" + "0" * keep
        b = __buf(keep)
        __putsub(b, result, 0, end - 1)
        __put(b, __byte(result, end - 1) + 1)
        __puts(b, "0" * (keep - end))
        return __done(b)
    return result if len(result) > 0 else "0"


def __fmt_fixed(digits: str, point: i32, precision: i32, alternate: bool) -> str:
    coefficient = __fmt_round(digits, point + precision)
    coefficient = coefficient.rjust(precision + 1, "0")
    if precision == 0:
        return coefficient + ("." if alternate else "")
    return coefficient[:-precision] + "." + coefficient[-precision:]


def __fmt_significant(digits: str, point: i32, precision: i32, kind: i32, alternate: bool) -> str:
    scientific = kind == 101 or kind == 69
    count = precision + 1 if scientific else max(precision, 1)
    rounded = __fmt_round(digits, count)
    if len(rounded) > count:
        rounded = rounded[:count]
        point += 1
    exponent = point - 1
    if not scientific:
        limit = count - 1 if kind == 0 else count
        scientific = exponent < -4 or exponent >= limit
        if not alternate:
            rounded = rounded.rstrip("0")
            if len(rounded) == 0:
                rounded = "0"
    if scientific:
        text = rounded[0]
        if len(rounded) > 1 or alternate:
            text += "." + rounded[1:]
        marker = "E" if kind == 69 or kind == 71 else "e"
        return text + marker + ("-" if exponent < 0 else "+") + str(abs(exponent)).zfill(2)
    if point <= 0:
        return "0." + "0" * -point + rounded
    if point >= len(rounded):
        return rounded + "0" * (point - len(rounded)) + (".0" if kind == 0 and not alternate else "." if alternate else "")
    return rounded[:point] + "." + rounded[point:]


def __fmt_float(v: f64, kind: i32, precision: i32, alternate: bool, normalize_zero: bool,
                sign: i32, width: i32, align: i32, fill: str, group: str) -> str:
    if kind == 37:
        v *= 100.0
    negative = __f64_bits(v) < 0 and v == v
    upper = kind == 69 or kind == 70 or kind == 71
    if v != v:
        text = "NAN" if upper else "nan"
    elif abs(v) == 1e999:
        text = "INF" if upper else "inf"
    elif kind == 0 and precision < 0:
        # No explicit precision: keep the existing shortest-repr behavior.
        text = str(abs(v))
        if alternate and "." not in text:
            pos = text.find("e")
            text = text[:pos] + "." + text[pos:] if pos >= 0 else text + "."
        if normalize_zero and v == 0.0:
            negative = False
    else:
        digits, point = __fmt_exact(abs(v))
        p = precision if precision >= 0 else 6
        if kind == 102 or kind == 70 or kind == 37:
            text = __fmt_fixed(digits, point, p, alternate)
        else:
            text = __fmt_significant(digits, point, p, kind, alternate)
        if normalize_zero:
            nonzero = False
            for ch in text:
                if ch == "e" or ch == "E":
                    break
                if ch >= "1" and ch <= "9":
                    nonzero = True
            if not nonzero:
                negative = False
    if kind == 37:
        text += "%"
    prefix = "-" if negative else "+" if sign == 43 else " " if sign == 32 else ""
    end = 0
    while end < len(text) and __byte(text, end) >= 48 and __byte(text, end) <= 57:
        end += 1
    if end == 0:
        return __fmt_align(prefix + text, width, align, fill, len(prefix))
    return __fmt_number(prefix, text[:end], text[end:], width, align, fill, group, 3)
'''
