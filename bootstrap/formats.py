"""Static format-spec validation in Snek; no regex or host format() call."""
from __future__ import annotations
from snek import i32, i64
from bootstrap.parser import Node
from bootstrap.declarations import declaration_error


class BodyFormat:
    kind: str
    precision: i32
    alternate: bool
    normalize_zero: bool
    sign: str
    width: i32
    align: str
    fill: str
    group: str

    def __init__(self) -> None:
        self.kind = ""
        self.precision = -1
        self.alternate = False
        self.normalize_zero = False
        self.sign = "-"
        self.width = 0
        self.align = ""
        self.fill = ""
        self.group = ""


def format_size(text: str, node: Node) -> i32:
    significant = text.lstrip("0")
    if len(significant) > 10:
        declaration_error("format width and precision must fit in i32", node)
    value = i64(significant) if len(significant) > 0 else i64(0)
    if value > 2147483647:
        declaration_error("format width and precision must fit in i32", node)
    return i32(value)


def body_format(text: str, ty: str, node: Node) -> BodyFormat:
    result = BodyFormat()
    pos = 0
    size = len(text)
    if size >= 2 and text[1] in "<>=^":
        result.fill = text[0]
        result.align = text[1]
        pos = 2
    elif size > 0 and text[0] in "<>=^":
        result.align = text[0]
        pos = 1
    explicit_sign = False
    if pos < size and text[pos] in "+- ":
        explicit_sign = True
        result.sign = text[pos]
        pos += 1
    if pos < size and text[pos] == "z":
        result.normalize_zero = True
        pos += 1
    if pos < size and text[pos] == "#":
        result.alternate = True
        pos += 1
    zero = False
    if pos < size and text[pos] == "0":
        zero = True
        pos += 1
    start = pos
    while pos < size and text[pos] in "0123456789":
        pos += 1
    result.width = format_size(text[start:pos], node)
    if pos < size and text[pos] in "_,":
        result.group = text[pos]
        pos += 1
    if pos < size and text[pos] == ".":
        pos += 1
        start = pos
        while pos < size and text[pos] in "0123456789":
            pos += 1
        if pos == start:
            declaration_error("invalid or unsupported format specifier", node)
        result.precision = format_size(text[start:pos], node)
    if pos < size and text[pos] in "bcdeEfFgGnosxX%":
        result.kind = text[pos]
        pos += 1
    if pos != size:
        declaration_error("invalid or unsupported format specifier", node)
    if result.kind == "n":
        declaration_error("locale-aware 'n' formatting is not supported; use d or g", node)
    floating = ty in ("f32", "f64") or result.kind in ("e", "E", "f", "F", "g", "G", "%")
    invalid = False
    if ty == "str":
        invalid = (result.kind not in ("", "s") or explicit_sign or result.normalize_zero
                   or result.alternate or result.align == "=" or result.group != "")
    elif floating:
        invalid = result.kind not in ("", "e", "E", "f", "F", "g", "G", "%")
    else:
        invalid = result.kind not in ("", "b", "c", "d", "o", "x", "X") or result.precision >= 0 or result.normalize_zero
        if result.kind == "c":
            invalid = invalid or explicit_sign or result.alternate or result.group != ""
        elif result.kind in ("b", "o", "x", "X"):
            invalid = invalid or result.group == ","
    if invalid:
        declaration_error("invalid format specifier for " + ty, node)
    if result.fill == "":
        result.fill = "0" if zero else " "
    if result.align == "":
        result.align = "<" if ty == "str" else "=" if zero else ">"
    return result
