"""Parse static format specs, and provide format helpers written in snek.

Explicit float precision uses exact decimal digits of the binary value, then
rounds ties to even. It does not round an already-rounded str(float).
"""

import re
from dataclasses import dataclass

from .errors import CompileError
from .runtime_sources import load_runtime_source
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


FORMAT_SOURCE = load_runtime_source("formatting.snek")
