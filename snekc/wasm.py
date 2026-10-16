"""Dependency-free binary encoding for the folded WAT emitted by snek.

This is a compiler backend, not a general-purpose WAT assembler. Keeping the
small text frontend lets the emitter and hand-written runtime share one path
for readable WAT and binary output. Unknown syntax fails closed. Validation of
stack types, subtyping and constant expressions remains the engine's job.
"""
from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field
from fractions import Fraction


class EncodeError(Exception):
    pass


class Form(list):
    def __init__(self, line: int):
        super().__init__()
        self.line = line


type Node = str | bytes | Form


def fail(message: str, node: Node | None = None):
    location = f" at WAT line {node.line}" if isinstance(node, Form) else ""
    raise EncodeError(message + location)


def require(condition: bool, message: str, node: Node | None = None):
    if not condition:
        fail(message, node)


def head(node: Node) -> str:
    return node[0] if isinstance(node, Form) and node and isinstance(node[0], str) else ""


def named(node: Node) -> bool:
    return isinstance(node, str) and node.startswith("$")


def definition(node: Form) -> tuple[str | None, list[Node]]:
    return (node[1], node[2:]) if len(node) > 1 and named(node[1]) else (None, node[1:])


_TOKEN = re.compile(r'\s+|;;[^\n]*|\(;|;\)|[()]|"(?:[^"\\]|\\.)*"|[^\s()";]+')
_ESCAPE = re.compile(r'\\([0-9a-fA-F]{2}|u\{[0-9a-fA-F_]+\}|[tnr"\'\\])')


def string_bytes(token: str) -> bytes:
    text = token[1:-1]
    out = bytearray()
    pos = 0
    for match in _ESCAPE.finditer(text):
        plain = text[pos:match.start()]
        require("\\" not in plain, "invalid string escape")
        out.extend(plain.encode("utf-8"))
        escape = match[1]
        if escape.startswith("u{"):
            out.extend(chr(int(escape[2:-1].replace("_", ""), 16)).encode("utf-8"))
        elif len(escape) == 2:
            out.append(int(escape, 16))
        else:
            out.extend({"t": b"\t", "n": b"\n", "r": b"\r", '"': b'"', "'": b"'", "\\": b"\\"}[escape])
        pos = match.end()
    require("\\" not in text[pos:], "invalid string escape")
    out.extend(text[pos:].encode("utf-8"))
    return bytes(out)


def parse(wat: str) -> Form:
    """Read s-expressions; string literals are bytes, identifiers are strings."""
    root = Form(1)
    stack = [root]
    pos, line, comments = 0, 1, 0
    while pos < len(wat):
        # Block comments nest and may contain unmatched quotes/parentheses.
        if comments:
            if wat.startswith("(;", pos):
                comments += 1
                pos += 2
            elif wat.startswith(";)", pos):
                comments -= 1
                pos += 2
            else:
                line += wat[pos] == "\n"
                pos += 1
            continue
        match = _TOKEN.match(wat, pos)
        require(match is not None, f"invalid token at WAT line {line}")
        token = match[0]
        pos = match.end()
        if token == "(;":
            comments = 1
        elif token.isspace() or token.startswith(";;"):
            pass
        elif token == "(":
            node = Form(line)
            stack[-1].append(node)
            stack.append(node)
        elif token == ")":
            require(len(stack) > 1, f"unexpected ')' at WAT line {line}")
            stack.pop()
        elif token.startswith('"'):
            stack[-1].append(string_bytes(token))
        else:
            require(token != ";)", f"unexpected comment end at WAT line {line}")
            stack[-1].append(token)
        line += token.count("\n")
    require(not comments, "unterminated block comment")
    require(len(stack) == 1, "unclosed form", stack[-1])
    require(len(root) == 1 and head(root[0]) == "module", "expected one core module")
    return root[0]


def uint(text: Node, bits: int = 32) -> int:
    require(isinstance(text, str), "expected integer")
    value = int(text.replace("_", ""), 16 if "0x" in text.lower() else 10)
    require(0 <= value < 1 << bits, f"integer out of u{bits} range: {text}")
    return value


def uleb(value: int) -> bytes:
    require(0 <= value <= 0xffffffff, "unsigned LEB128 out of u32 range")
    out = bytearray()
    while value >= 128:
        out.append((value & 127) | 128)
        value >>= 7
    out.append(value)
    return bytes(out)


def sleb(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 127
        value >>= 7
        if (value == 0 and not byte & 64) or (value == -1 and byte & 64):
            out.append(byte)
            return bytes(out)
        out.append(byte | 128)


def vec(items) -> bytes:
    items = list(items)
    return uleb(len(items)) + b"".join(items)


def blob(data: bytes) -> bytes:
    return uleb(len(data)) + data


def section(number: int, data: bytes) -> bytes:
    return bytes([number]) + blob(data)


@dataclass
class Index:
    kind: str
    names: dict[str, int] = field(default_factory=dict)
    count: int = 0

    def add(self, name: str | None) -> int:
        index = self.count
        self.count += 1
        if name is not None:
            require(name not in self.names, f"duplicate {self.kind} {name}")
            self.names[name] = index
        return index

    def get(self, name: Node) -> int:
        if named(name):
            require(name in self.names, f"unknown {self.kind} {name}")
            return self.names[name]
        index = uint(name)
        require(index < self.count, f"{self.kind} index out of range: {index}")
        return index

    def debug_names(self) -> bytes:
        return vec(uleb(i) + blob(n[1:].encode()) for n, i in self.names.items())


# The core binary format's numeric opcodes are contiguous, in this order.
_NUMERIC = """
i32.eqz i32.eq i32.ne i32.lt_s i32.lt_u i32.gt_s i32.gt_u i32.le_s i32.le_u i32.ge_s i32.ge_u
i64.eqz i64.eq i64.ne i64.lt_s i64.lt_u i64.gt_s i64.gt_u i64.le_s i64.le_u i64.ge_s i64.ge_u
f32.eq f32.ne f32.lt f32.gt f32.le f32.ge f64.eq f64.ne f64.lt f64.gt f64.le f64.ge
i32.clz i32.ctz i32.popcnt i32.add i32.sub i32.mul i32.div_s i32.div_u i32.rem_s i32.rem_u
i32.and i32.or i32.xor i32.shl i32.shr_s i32.shr_u i32.rotl i32.rotr
i64.clz i64.ctz i64.popcnt i64.add i64.sub i64.mul i64.div_s i64.div_u i64.rem_s i64.rem_u
i64.and i64.or i64.xor i64.shl i64.shr_s i64.shr_u i64.rotl i64.rotr
f32.abs f32.neg f32.ceil f32.floor f32.trunc f32.nearest f32.sqrt
f32.add f32.sub f32.mul f32.div f32.min f32.max f32.copysign
f64.abs f64.neg f64.ceil f64.floor f64.trunc f64.nearest f64.sqrt
f64.add f64.sub f64.mul f64.div f64.min f64.max f64.copysign
i32.wrap_i64 i32.trunc_f32_s i32.trunc_f32_u i32.trunc_f64_s i32.trunc_f64_u
i64.extend_i32_s i64.extend_i32_u i64.trunc_f32_s i64.trunc_f32_u i64.trunc_f64_s i64.trunc_f64_u
f32.convert_i32_s f32.convert_i32_u f32.convert_i64_s f32.convert_i64_u f32.demote_f64
f64.convert_i32_s f64.convert_i32_u f64.convert_i64_s f64.convert_i64_u f64.promote_f32
i32.reinterpret_f32 i64.reinterpret_f64 f32.reinterpret_i32 f64.reinterpret_i64
i32.extend8_s i32.extend16_s i64.extend8_s i64.extend16_s i64.extend32_s
""".split()
SIMPLE = {name: bytes([code]) for code, name in enumerate(_NUMERIC, 0x45)}
SIMPLE.update({"unreachable": b"\x00", "nop": b"\x01", "return": b"\x0f",
               "drop": b"\x1a", "select": b"\x1b", "ref.is_null": b"\xd1",
               "ref.eq": b"\xd3", "ref.as_non_null": b"\xd4", "array.len": b"\xfb\x0f"})
SIMPLE.update({f"{dst}.trunc_sat_{src}_{sign}": bytes([0xfc, n])
               for n, (dst, src, sign) in enumerate(
                   (d, s, u) for d in ("i32", "i64") for s in ("f32", "f64") for u in ("s", "u"))})

_MEMORY = """i32.load i64.load f32.load f64.load i32.load8_s i32.load8_u i32.load16_s i32.load16_u
i64.load8_s i64.load8_u i64.load16_s i64.load16_u i64.load32_s i64.load32_u
i32.store i64.store f32.store f64.store i32.store8 i32.store16 i64.store8 i64.store16 i64.store32""".split()
MEMORY = {name: (code, align) for name, code, align in zip(
    _MEMORY, range(0x28, 0x3f), (2, 3, 2, 3, 0, 0, 1, 1, 0, 0, 1, 1, 2, 2, 2, 3, 2, 3, 0, 1, 0, 1, 2))}
GC = {"struct.new": (0, 1), "struct.new_default": (1, 1), "struct.get": (2, 2),
      "struct.get_s": (3, 2), "struct.get_u": (4, 2), "struct.set": (5, 2),
      "array.new": (6, 1), "array.new_default": (7, 1), "array.new_fixed": (8, 2),
      "array.new_data": (9, 2), "array.get": (11, 1), "array.get_s": (12, 1),
      "array.get_u": (13, 1), "array.set": (14, 1), "array.fill": (16, 1),
      "array.copy": (17, 2), "array.init_data": (18, 2)}
VALUES = {"i32": b"\x7f", "i64": b"\x7e", "f32": b"\x7d", "f64": b"\x7c"}
HEAPS = {"func": 0x70, "extern": 0x6f, "any": 0x6e, "eq": 0x6d, "i31": 0x6c,
         "struct": 0x6b, "array": 0x6a, "none": 0x71, "noextern": 0x72, "nofunc": 0x73,
         "exn": 0x69, "noexn": 0x74}


class Module:
    def __init__(self, wat: str):
        _, self.forms = definition(parse(wat))
        self.spaces = {kind: Index(kind) for kind in ("type", "func", "memory", "global", "tag", "data")}
        self.fields: dict[int, Index] = {}
        self.type_entries: list[bytes] = []
        self.signatures: dict[int, tuple[tuple[bytes, ...], tuple[bytes, ...]]] = {}
        self.implicit: dict[tuple, int] = {}
        self.indices: dict[int, int] = {}
        self.func_types: dict[int, int] = {}
        self.exports: list[bytes] = []
        self.export_names: set[bytes] = set()
        self.local_names: list[bytes] = []

    def index(self, kind: str, name: Node) -> int:
        return self.spaces[kind].get(name)

    def heap(self, node: Node) -> bytes:
        if isinstance(node, str) and node in HEAPS:
            return bytes([HEAPS[node]])
        return sleb(self.index("type", node))  # heap indices are signed s33, not u32

    def value(self, node: Node) -> bytes:
        if isinstance(node, str):
            if node in VALUES:
                return VALUES[node]
            if node.endswith("ref") and node[:-3] in HEAPS:
                return bytes([HEAPS[node[:-3]]])
        if head(node) == "ref":
            nullable = len(node) == 3 and node[1] == "null"
            require(len(node) == (3 if nullable else 2), "invalid reference type", node)
            heap = self.heap(node[-1])
            # Abstract nullable references have a compact one-byte spelling.
            return heap if nullable and node[-1] in HEAPS else bytes([0x63 if nullable else 0x64]) + heap
        fail(f"unsupported value type {node}", node)

    def field_type(self, node: Node) -> bytes:
        mutable = head(node) == "mut"
        if mutable:
            require(len(node) == 2, "invalid mutable field", node)
            node = node[1]
        storage = {"i8": b"\x78", "i16": b"\x77"}.get(node) if isinstance(node, str) else None
        return (storage or self.value(node)) + bytes([mutable])

    def bindings(self, forms: list[Node], kind: str, space: Index | None = None) -> tuple[bytes, ...]:
        types = []
        for form in forms:
            if head(form) != kind:
                continue
            name, values = definition(form)
            require(bool(values) and (name is None or len(values) == 1), f"invalid {kind}", form)
            for value in values:
                if space is not None:
                    space.add(name)
                types.append(self.value(value))
        return tuple(types)

    def signature(self, forms: list[Node]) -> tuple[tuple[bytes, ...], tuple[bytes, ...]]:
        return self.bindings(forms, "param"), self.bindings(forms, "result")

    def intern(self, signature: tuple) -> int:
        if signature not in self.implicit:
            index = self.spaces["type"].add(None)
            self.implicit[signature] = index
            self.signatures[index] = signature
            self.type_entries.append(b"\x60" + vec(signature[0]) + vec(signature[1]))
        return self.implicit[signature]

    def type_use(self, forms: list[Node]) -> int:
        uses = [f for f in forms if head(f) == "type"]
        require(len(uses) <= 1, "duplicate type use")
        if not uses:
            return self.intern(self.signature(forms))
        require(len(uses[0]) == 2, "invalid type use", uses[0])
        index = self.index("type", uses[0][1])
        require(index in self.signatures, "type use must name a function type", uses[0])
        if any(head(f) in ("param", "result") for f in forms):
            require(self.signature(forms) == self.signatures[index], "inline signature does not match type use", uses[0])
        return index

    def encode_type(self, form: Form) -> bytes:
        _, parts = definition(form)
        require(len(parts) == 1, "invalid type definition", form)
        body, prefix = parts[0], b""
        if head(body) == "sub":
            parts = body[1:]
            final = bool(parts and parts[0] == "final")
            if final:
                parts = parts[1:]
            require(1 <= len(parts) <= 2, "invalid subtype", body)
            supers = parts[:-1]
            prefix = bytes([0x4f if final else 0x50]) + vec(uleb(self.index("type", s)) for s in supers)
            body = parts[-1]
        index = self.indices[id(form)]
        kind = head(body)
        if kind == "func":
            require(all(head(p) in ("param", "result") for p in body[1:]), "invalid function type", body)
            signature = self.signature(body[1:])
            self.signatures[index] = signature
            code = b"\x60" + vec(signature[0]) + vec(signature[1])
        elif kind == "struct":
            fields, space = [], Index("field")
            for f in body[1:]:
                require(head(f) == "field", "expected struct field", body)
                name, values = definition(f)
                require(len(values) == 1, "expected one field type", f)
                space.add(name)
                fields.append(self.field_type(values[0]))
            self.fields[index] = space
            code = b"\x5f" + vec(fields)
        elif kind == "array":
            require(len(body) == 2, "expected array element type", body)
            code = b"\x5e" + self.field_type(body[1])
        else:
            fail(f"unsupported composite type {kind}", form)
        return prefix + code

    def collect(self):
        # Resolve every explicit index first, including forward references in rec groups.
        groups = []
        imports, definitions = [], []
        for form in self.forms:
            kind = head(form)
            if kind in ("type", "rec"):
                group = form[1:] if kind == "rec" else [form]
                require(bool(group), "empty recursion group", form)
                for ty in group:
                    require(head(ty) == "type", "expected type in rec group", form)
                    name, _ = definition(ty)
                    self.indices[id(ty)] = self.spaces["type"].add(name)
                groups.append((kind, group))
            elif kind == "import":
                require(len(form) == 4 and isinstance(form[1], bytes) and isinstance(form[2], bytes), "invalid import", form)
                imports.append(form[3])
            elif kind in ("func", "memory", "global", "tag", "data"):
                definitions.append(form)
            elif kind not in ("export", "elem", "start"):
                fail(f"unsupported module field {kind}", form)
        for kind, group in groups:
            types = [self.encode_type(ty) for ty in group]
            self.type_entries.append(b"\x4e" + vec(types) if kind == "rec" else types[0])
        for form in imports:
            require(head(form) in ("func", "memory", "global", "tag"), "unsupported import", form)
        for form in imports + definitions:
            kind = head(form)
            name, _ = definition(form)
            self.indices[id(form)] = self.spaces[kind].add(name)
        for form in imports + definitions:
            if head(form) in ("func", "tag"):
                _, parts = definition(form)
                self.func_types[id(form)] = self.type_use(parts)

    def export(self, name: Node, kind: str, index: int):
        require(isinstance(name, bytes), "expected export name string")
        require(kind in ("func", "memory", "global", "tag"), f"unsupported export kind {kind}")
        require(name not in self.export_names, "duplicate export name")
        self.export_names.add(name)
        self.exports.append(blob(name) + bytes([{"func": 0, "memory": 2, "global": 3, "tag": 4}[kind]]) + uleb(index))

    def parts(self, form: Form) -> list[Node]:
        _, parts = definition(form)
        for p in parts:
            if head(p) == "export":
                require(len(p) == 2, "invalid inline export", p)
                self.export(p[1], head(form), self.indices[id(form)])
        return [p for p in parts if head(p) != "export"]

    def memory(self, parts: list[Node]) -> bytes:
        require(1 <= len(parts) <= 2, "only memory32 limits are supported")
        limits = [uint(p) for p in parts]
        require(all(n <= 65536 for n in limits) and limits[0] <= limits[-1], "invalid memory limits")
        return bytes([len(limits) - 1]) + b"".join(uleb(n) for n in limits)

    def function(self, form: Form, parts: list[Node]) -> bytes:
        locals_ = Index("local")
        params = self.bindings(parts, "param", locals_)
        if not params:
            for _ in self.signatures[self.func_types[id(form)]][0]:
                locals_.add(None)
        local_types = self.bindings(parts, "local", locals_)
        groups = []
        for ty in local_types:
            if groups and groups[-1][1] == ty:
                groups[-1][0] += 1
            else:
                groups.append([1, ty])
        body = [p for p in parts if head(p) not in ("param", "result", "type", "local")]
        instructions = Instructions(self, locals_, [None]).sequence(body)
        self.local_names.append(uleb(self.indices[id(form)]) + locals_.debug_names())
        return blob(vec(uleb(n) + ty for n, ty in groups) + instructions + b"\x0b")

    def encode(self, *, debug_names: bool = True) -> bytes:
        self.collect()
        imports, functions, memories, tags, globals_, elements, codes, data = ([] for _ in range(8))
        start = None
        for form in self.forms:
            kind = head(form)
            if kind in ("type", "rec"):
                continue
            if kind == "import":
                desc = form[3]
                parts = self.parts(desc)
                desc_kind = head(desc)
                if desc_kind == "func":
                    require(all(head(p) in ("type", "param", "result") for p in parts), "invalid function import", desc)
                    encoded = b"\x00" + uleb(self.func_types[id(desc)])
                elif desc_kind == "memory":
                    encoded = b"\x02" + self.memory(parts)
                elif desc_kind == "global":
                    require(len(parts) == 1, "invalid global import", desc)
                    encoded = b"\x03" + self.field_type(parts[0])
                else:
                    require(all(head(p) in ("type", "param") for p in parts), "invalid tag import", desc)
                    encoded = b"\x04\x00" + uleb(self.func_types[id(desc)])
                imports.append(blob(form[1]) + blob(form[2]) + encoded)
                continue
            parts = self.parts(form) if kind in self.spaces else form[1:]
            if kind == "func":
                functions.append(uleb(self.func_types[id(form)]))
                codes.append(self.function(form, parts))
            elif kind == "memory":
                memories.append(self.memory(parts))
            elif kind == "tag":
                require(all(head(p) in ("type", "param") for p in parts), "invalid tag", form)
                tags.append(b"\x00" + uleb(self.func_types[id(form)]))
            elif kind == "global":
                require(len(parts) >= 2, "missing global initializer", form)
                globals_.append(self.field_type(parts[0]) + Instructions(self).sequence(parts[1:]) + b"\x0b")
            elif kind == "data":
                require(all(isinstance(p, bytes) for p in parts), "only passive data segments are supported", form)
                data.append(b"\x01" + blob(b"".join(parts)))
            elif kind == "elem":
                require(parts[:2] == ["declare", "func"], "only declarative function elements are supported", form)
                elements.append(b"\x03\x00" + vec(uleb(self.index("func", p)) for p in parts[2:]))
            elif kind == "export":
                require(len(parts) == 2 and head(parts[1]) in ("func", "memory", "global", "tag")
                        and len(parts[1]) == 2, "invalid export", form)
                self.export(parts[0], head(parts[1]), self.index(head(parts[1]), parts[1][1]))
            elif kind == "start":
                require(start is None and len(parts) == 1, "invalid or duplicate start", form)
                start = uleb(self.index("func", parts[0]))
        # Tag and data-count sections have special ordering, not numeric ID order.
        out = bytearray(b"\0asm\x01\0\0\0")
        for number, entries in ((1, self.type_entries), (2, imports), (3, functions), (5, memories),
                                (13, tags), (6, globals_), (7, self.exports)):
            if entries:
                out.extend(section(number, vec(entries)))
        if start is not None:
            out.extend(section(8, start))
        if elements:
            out.extend(section(9, vec(elements)))
        if data:
            out.extend(section(12, uleb(len(data))))
        if codes:
            out.extend(section(10, vec(codes)))
        if data:
            out.extend(section(11, vec(data)))
        name_parts = {}
        for number, kind in ((1, "func"), (4, "type"), (6, "memory"), (7, "global"), (9, "data"), (11, "tag")):
            if self.spaces[kind].names:
                name_parts[number] = self.spaces[kind].debug_names()
        if self.local_names:
            name_parts[2] = vec(self.local_names)
        if self.fields:
            name_parts[10] = vec(uleb(i) + f.debug_names() for i, f in self.fields.items())
        if name_parts and debug_names:
            names = blob(b"name") + b"".join(section(i, name_parts[i]) for i in sorted(name_parts))
            out.extend(section(0, names))
        return bytes(out)


def float32_bits(text: str) -> int:
    """Round a positive finite WAT literal directly to f32, never via f64."""
    if text.startswith("0x"):
        require(re.fullmatch(r"0x(?:[0-9a-f]+(?:\.[0-9a-f]*)?|\.[0-9a-f]+)(?:p[+-]?[0-9]+)?", text) is not None,
                "invalid hexadecimal float")
        mantissa, separator, exponent = text[2:].partition("p")
        power = int(exponent) if separator else 0
        if "." in mantissa:
            whole, fractional = mantissa.split(".")
            mantissa = whole + fractional
            power -= 4 * len(fractional)
        value = Fraction(int(mantissa, 16))
        value = value * (1 << power) if power >= 0 else value / (1 << -power)
    else:
        require(re.fullmatch(r"(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:e[+-]?[0-9]+)?", text) is not None,
                "invalid decimal float")
        value = Fraction(text)
    n, d = value.numerator, value.denominator
    if n == 0:
        return 0
    exponent = n.bit_length() - d.bit_length()
    if (n << max(-exponent, 0)) < (d << max(exponent, 0)):
        exponent -= 1
    target = max(exponent, -126)
    shift = 23 - target
    n <<= max(shift, 0)
    d <<= max(-shift, 0)
    significand, remainder = divmod(n, d)
    if 2 * remainder > d or (2 * remainder == d and significand & 1):
        significand += 1
    if significand == 1 << 24:
        significand >>= 1
        target += 1
    require(target <= 127, "floating constant out of f32 range")
    if significand < 1 << 23:
        return significand
    return ((target + 127) << 23) | (significand - (1 << 23))


def constant(op: str, node: Node) -> bytes:
    require(isinstance(node, str), "expected numeric constant")
    text = node.replace("_", "").lower()
    bits = int(op[1:3])
    if op[0] == "i":
        value = int(text, 16 if "0x" in text else 10)
        require(-(1 << (bits - 1)) <= value < 1 << bits, f"constant out of i{bits} range")
        if value >= 1 << (bits - 1):
            value -= 1 << bits
        return bytes([0x41 if bits == 32 else 0x42]) + sleb(value)
    sign = 1 << (bits - 1) if text.startswith("-") else 0
    unsigned = text[1:] if text.startswith(("+", "-")) else text
    if unsigned.startswith("nan"):
        fraction = 23 if bits == 32 else 52
        payload = (1 << (fraction - 1)) if unsigned == "nan" else uint(unsigned.removeprefix("nan:"), bits)
        require(0 < payload < 1 << fraction, "invalid NaN payload")
        value_bytes = (sign | (((1 << (bits - fraction - 1)) - 1) << fraction) | payload).to_bytes(bits // 8, "little")
    elif bits == 32 and unsigned != "inf":
        value_bytes = (sign | float32_bits(unsigned)).to_bytes(4, "little")
    else:
        value = float.fromhex(text) if "0x" in text else float(text)
        require(unsigned == "inf" or value not in (float("inf"), -float("inf")), "floating constant out of range")
        value_bytes = struct.pack("<f" if bits == 32 else "<d", value)
    return bytes([0x43 if bits == 32 else 0x44]) + value_bytes


class Instructions:
    def __init__(self, module: Module, locals_: Index | None = None, labels: list[str | None] | None = None):
        self.module = module
        self.locals = locals_ or Index("local")
        self.labels = labels or []

    def label(self, node: Node) -> bytes:
        if named(node):
            for depth, label in enumerate(reversed(self.labels)):
                if node == label:
                    return uleb(depth)
            fail(f"unknown label {node}")
        depth = uint(node)
        require(depth < len(self.labels), f"label depth out of range: {depth}")
        return uleb(depth)

    def sequence(self, nodes: list[Node]) -> bytes:
        return b"".join(self.instruction(n) for n in nodes)

    def control(self, node: Form) -> bytes:
        op = node[0]
        label, parts = definition(node)
        declarations = []
        while parts and head(parts[0]) in ("type", "param", "result"):
            declarations.append(parts.pop(0))
        params, results = self.module.signature(declarations)
        if any(head(d) == "type" for d in declarations) or params or len(results) > 1:
            block_type = sleb(self.module.type_use(declarations))
        else:
            block_type = results[0] if results else b"\x40"
        prefix = b""
        if op == "if":
            then = next((i for i, p in enumerate(parts) if head(p) == "then"), None)
            require(then is not None, "if needs a then arm", node)
            prefix = self.sequence(parts[:then])  # condition executes outside the if label
            arms = parts[then:]
            require(len(arms) in (1, 2) and (len(arms) == 1 or head(arms[1]) == "else"), "invalid if arms", node)
        catches = []
        if op == "try_table":
            # Catch targets are relative to the OUTSIDE of the try_table;
            # branches in its body include the try_table's own label.
            while parts and head(parts[0]) in ("catch", "catch_ref", "catch_all", "catch_all_ref"):
                catch = parts.pop(0)
                kind = head(catch)
                tagged = kind in ("catch", "catch_ref")
                require(len(catch) == (3 if tagged else 2), "invalid catch", catch)
                tag = uleb(self.module.index("tag", catch[1])) if tagged else b""
                catches.append(bytes([{"catch": 0, "catch_ref": 1, "catch_all": 2, "catch_all_ref": 3}[kind]])
                               + tag + self.label(catch[-1]))
        self.labels.append(label)
        try:
            if op == "if":
                body = self.sequence(arms[0][1:])
                if len(arms) == 2:
                    body += b"\x05" + self.sequence(arms[1][1:])
            elif op == "try_table":
                body = vec(catches) + self.sequence(parts)
            else:
                body = self.sequence(parts)
        finally:
            self.labels.pop()
        return prefix + bytes([{"block": 2, "loop": 3, "if": 4, "try_table": 0x1f}[op]]) + block_type + body + b"\x0b"

    def instruction(self, node: Node) -> bytes:
        require(isinstance(node, Form) and bool(node), "expected folded instruction", node)
        try:
            return self.encode_instruction(node)
        except EncodeError as e:
            if " at WAT line " in str(e):
                raise
            fail(str(e), node)
        except (ValueError, OverflowError, IndexError, TypeError) as e:
            fail(f"invalid {head(node)} instruction: {e}", node)

    def encode_instruction(self, node: Form) -> bytes:
        op, args = node[0], node[1:]
        if op in ("block", "loop", "if", "try_table"):
            return self.control(node)
        if op in SIMPLE:
            return self.sequence(args) + SIMPLE[op]
        if op in ("memory.size", "memory.grow"):
            memory = args.pop(0) if args and isinstance(args[0], str) else "0"
            index = self.module.index("memory", memory)
            return self.sequence(args) + bytes([0x3f if op == "memory.size" else 0x40]) + uleb(index)
        if op in ("i32.const", "i64.const", "f32.const", "f64.const"):
            require(len(args) == 1, "expected one constant")
            return constant(op, args[0])
        if op in ("local.get", "local.set", "local.tee", "global.get", "global.set", "call", "call_ref", "ref.func", "throw"):
            kind, code = {"local.get": ("local", 0x20), "local.set": ("local", 0x21), "local.tee": ("local", 0x22),
                          "global.get": ("global", 0x23), "global.set": ("global", 0x24), "call": ("func", 0x10),
                          "call_ref": ("type", 0x14), "ref.func": ("func", 0xd2), "throw": ("tag", 0x08)}[op]
            index = self.locals.get(args[0]) if kind == "local" else self.module.index(kind, args[0])
            return self.sequence(args[1:]) + bytes([code]) + uleb(index)
        if op in ("br", "br_if"):
            return self.sequence(args[1:]) + bytes([0x0c if op == "br" else 0x0d]) + self.label(args[0])
        if op == "ref.null":
            require(len(args) == 1, "expected heap type")
            return b"\xd0" + self.module.heap(args[0])
        if op in ("ref.test", "ref.cast"):
            ref = args[0]
            require(head(ref) == "ref", "expected reference type")
            self.module.value(ref)  # check nullability syntax
            code = (0x14 if op == "ref.test" else 0x16) + (len(ref) == 3)
            return self.sequence(args[1:]) + bytes([0xfb, code]) + self.module.heap(ref[-1])
        if op in GC:
            code, count = GC[op]
            index = self.module.index("type", args[0])
            immediate = uleb(index)
            if count == 2:
                if op.startswith("struct."):
                    require(index in self.module.fields, "expected struct type")
                    second = self.module.fields[index].get(args[1])
                elif op in ("array.new_data", "array.init_data"):
                    second = self.module.index("data", args[1])
                elif op == "array.new_fixed":
                    second = uint(args[1])
                else:
                    second = self.module.index("type", args[1])
                immediate += uleb(second)
            return self.sequence(args[count:]) + bytes([0xfb, code]) + immediate
        if op in MEMORY:
            code, alignment = MEMORY[op]
            offset, seen = 0, set()
            while args and isinstance(args[0], str):
                key, sep, value = args.pop(0).partition("=")
                require(sep == "=" and key in ("offset", "align") and key not in seen, "invalid memory argument")
                seen.add(key)
                number = uint(value)
                if key == "offset":
                    offset = number
                else:
                    require(number > 0 and number & (number - 1) == 0 and number <= 1 << alignment, "invalid memory alignment")
                    alignment = number.bit_length() - 1
            return self.sequence(args) + bytes([code]) + uleb(alignment) + uleb(offset)
        fail(f"unsupported instruction {op}", node)


def encode(wat: str, *, debug_names: bool = True) -> bytes:
    """Encode snek's core WAT without invoking any external tools."""
    try:
        return Module(wat).encode(debug_names=debug_names)
    except (ValueError, OverflowError, IndexError, TypeError) as e:
        raise EncodeError(f"invalid compiler WAT: {e}") from e
