"""Bootstrap binary backend, written entirely in the supported Snek language.

Reads compiler-generated folded core WAT from stdin and writes a Wasm binary
to stdout. No Python parser, regex engine, struct module, or assembler is used.
Debug-name custom sections are deliberately omitted from bootstrap artifacts.
"""
from __future__ import annotations
from snek import i32, i64, read_bytes, write_bytes


def need(ok: bool, message: str) -> None:
    if not ok:
        raise ValueError(message)


def uleb(out: list[i32], value: i64) -> None:
    need(value >= 0 and value <= 4294967295, "unsigned immediate out of range")
    while value >= 128:
        out.append(i32(value & 127) | 128)
        value >>= 7
    out.append(i32(value))


def sleb(out: list[i32], value: i64) -> None:
    while True:
        byte = i32(value & 127)
        value >>= 7
        if (value == 0 and (byte & 64) == 0) or (value == -1 and (byte & 64) != 0):
            out.append(byte)
            return
        out.append(byte | 128)


def blob(out: list[i32], data: list[i32]) -> None:
    uleb(out, len(data))
    out.extend(data)


def vector(items: list[list[i32]]) -> list[i32]:
    out: list[i32] = []
    uleb(out, len(items))
    for item in items:
        out.extend(item)
    return out


def section(out: list[i32], kind: i32, data: list[i32]) -> None:
    out.append(kind)
    blob(out, data)


def byte_key(data: list[i32]) -> str:
    chars: list[str] = []
    for byte in data:
        chars.append(chr(byte))
    return "".join(chars)


def digit(char: str) -> i32:
    if char >= "0" and char <= "9":
        return ord(char) - 48
    if char >= "a" and char <= "f":
        return ord(char) - 87
    if char >= "A" and char <= "F":
        return ord(char) - 55
    return -1


def unsigned(text: str) -> i64:
    value: i64 = 0
    maximum: i64 = 4294967295
    base = 10
    text = text.replace("_", "")
    if text.startswith("+"):
        text = text[1:]
    if text.startswith("0x"):
        base = 16
        text = text[2:]
    need(len(text) > 0, "expected unsigned integer")
    for char in text:
        d = digit(char)
        need(d >= 0 and d < base, "invalid unsigned integer")
        need(value <= (maximum - d) // base, "unsigned immediate out of range")
        value = value * base + d
    return value


class Node:
    kind: i32
    text: str
    data: list[i32]
    items: list[Node]
    line: i32
    index: i32
    signature: i32

    def __init__(self, kind: i32, text: str, line: i32) -> None:
        self.kind = kind
        self.text = text
        self.data = []
        self.items = []
        self.line = line
        self.index = -1
        self.signature = -1

    def head(self) -> str:
        if self.kind == 0 and len(self.items) > 0 and self.items[0].kind == 1:
            return self.items[0].text
        return ""

    def named(self) -> bool:
        return self.kind == 1 and self.text.startswith("$")

    def name(self) -> str:
        if len(self.items) > 1 and self.items[1].named():
            return self.items[1].text
        return ""

    def parts(self) -> list[Node]:
        if self.name() != "":
            return self.items[2:]
        return self.items[1:]


def utf8(out: list[i32], code: i32) -> None:
    need(code >= 0 and code <= 1114111 and not (code >= 55296 and code <= 57343), "invalid Unicode escape")
    if code < 128:
        out.append(code)
    elif code < 2048:
        out.extend([192 | (code >> 6), 128 | (code & 63)])
    elif code < 65536:
        out.extend([224 | (code >> 12), 128 | ((code >> 6) & 63), 128 | (code & 63)])
    else:
        out.extend([240 | (code >> 18), 128 | ((code >> 12) & 63), 128 | ((code >> 6) & 63), 128 | (code & 63)])


class Reader:
    data: list[i32]
    pos: i32
    line: i32

    def __init__(self, data: list[i32]) -> None:
        self.data = data
        self.pos = 0
        self.line = 1

    def peek(self) -> i32:
        if self.pos == len(self.data):
            return -1
        return self.data[self.pos]

    def take(self) -> i32:
        need(self.pos < len(self.data), "unexpected end of WAT")
        c = self.data[self.pos]
        self.pos += 1
        if c == 10:
            self.line += 1
        return c

    def pair(self, a: i32, b: i32) -> bool:
        return self.pos + 1 < len(self.data) and self.data[self.pos] == a and self.data[self.pos + 1] == b

    def quoted(self) -> Node:
        node = Node(2, "", self.line)
        self.take()
        escapes: dict[i32, i32] = {116: 9, 110: 10, 114: 13, 34: 34, 39: 39, 92: 92}
        while True:
            c = self.take()
            if c == 34:
                return node
            if c != 92:
                node.data.append(c)
            else:
                c = self.take()
                hi = digit(chr(c))
                if hi >= 0:
                    lo = digit(chr(self.take()))
                    need(lo >= 0, "invalid byte escape")
                    node.data.append(hi * 16 + lo)
                elif c == 117:
                    need(self.take() == 123, "expected Unicode escape brace")
                    code = 0
                    count = 0
                    while self.peek() != 125:
                        c = self.take()
                        if c != 95:
                            d = digit(chr(c))
                            need(d >= 0 and code <= 1114111 // 16, "invalid Unicode escape")
                            code = code * 16 + d
                            count += 1
                    self.take()
                    need(count > 0, "empty Unicode escape")
                    utf8(node.data, code)
                else:
                    need(c in escapes, "invalid string escape")
                    node.data.append(escapes[c])

    def parse(self) -> Node:
        root = Node(0, "", 1)
        stack: list[Node] = [root]
        while self.pos < len(self.data):
            c = self.peek()
            if c == 32 or c == 9 or c == 10 or c == 13 or c == 12:
                self.take()
            elif self.pair(59, 59):
                while self.peek() != 10 and self.peek() != -1:
                    self.take()
            elif self.pair(40, 59):
                self.pos += 2
                depth = 1
                while depth > 0:
                    if self.pair(40, 59):
                        depth += 1
                        self.pos += 2
                    elif self.pair(59, 41):
                        depth -= 1
                        self.pos += 2
                    else:
                        self.take()
            elif c == 40:
                self.take()
                node = Node(0, "", self.line)
                stack[-1].items.append(node)
                stack.append(node)
            elif c == 41:
                need(len(stack) > 1, "unexpected closing parenthesis")
                self.take()
                stack.pop()
            elif c == 34:
                stack[-1].items.append(self.quoted())
            else:
                chars: list[str] = []
                while c > 32 and c != 40 and c != 41 and c != 34 and c != 59:
                    need(c < 127, "identifiers must be ASCII")
                    chars.append(chr(self.take()))
                    c = self.peek()
                need(len(chars) > 0, "invalid WAT token")
                stack[-1].items.append(Node(1, "".join(chars), self.line))
        need(len(stack) == 1, "unclosed WAT form")
        need(len(root.items) == 1 and root.items[0].head() == "module", "expected one core module")
        return root.items[0]


# Small unsigned arbitrary-precision integers, base 2**15. They are used only
# for exact decimal/hexadecimal -> IEEE-754 rounding and 64-bit bit patterns.
def big_trim(value: list[i32]) -> None:
    while len(value) > 1 and value[-1] == 0:
        value.pop()


def big_mul(value: list[i32], factor: i32, carry: i32) -> None:
    for index in range(len(value)):
        n = value[index] * factor + carry
        value[index] = n & 32767
        carry = n >> 15
    while carry != 0:
        value.append(carry & 32767)
        carry >>= 15


def big_shift(value: list[i32], count: i32) -> list[i32]:
    out: list[i32] = []
    for index in range(count // 15):
        out.append(0)
    out.extend(value)
    big_mul(out, 1 << (count % 15), 0)
    big_trim(out)
    return out


def big_cmp(a: list[i32], b: list[i32]) -> i32:
    if len(a) != len(b):
        return -1 if len(a) < len(b) else 1
    for index in range(len(a) - 1, -1, -1):
        if a[index] != b[index]:
            return -1 if a[index] < b[index] else 1
    return 0


def big_sub(a: list[i32], b: list[i32]) -> None:
    borrow = 0
    for index in range(len(a)):
        n = a[index] - borrow
        if index < len(b):
            n -= b[index]
        borrow = 0
        if n < 0:
            n += 32768
            borrow = 1
        a[index] = n
    big_trim(a)


def big_bits(value: list[i32]) -> i32:
    n = value[-1]
    bits = (len(value) - 1) * 15
    while n > 0:
        bits += 1
        n >>= 1
    return bits


def big_parse(text: str, base: i32) -> list[i32]:
    out: list[i32] = [0]
    need(len(text) > 0, "expected number")
    for char in text:
        d = digit(char)
        need(d >= 0 and d < base, "invalid number")
        big_mul(out, base, d)
    big_trim(out)
    return out


def rounded_ratio(numerator: list[i32], denominator: list[i32], shift: i32) -> i64:
    n = big_shift(numerator, max(shift, 0))
    d = big_shift(denominator, max(-shift, 0))
    quotient: i64 = 0
    highest = big_bits(n) - big_bits(d)
    need(highest < 63, "floating significand overflow")
    for bit in range(highest, -1, -1):
        trial = big_shift(d, bit)
        if big_cmp(n, trial) >= 0:
            big_sub(n, trial)
            quotient |= i64(1) << bit
    cmp = big_cmp(big_shift(n, 1), d)
    if cmp > 0 or (cmp == 0 and (quotient & 1) != 0):
        quotient += 1
    return quotient


def float_bytes(text: str, width: i32) -> list[i32]:
    text = text.replace("_", "").lower()
    negative = text.startswith("-")
    if negative or text.startswith("+"):
        text = text[1:]
    precision = 23 if width == 32 else 52
    bias = 127 if width == 32 else 1023
    exponent = 0
    fraction: i64 = 0
    if text == "inf" or text.startswith("nan"):
        exponent = bias * 2 + 1
        if text == "nan":
            fraction = i64(1) << (precision - 1)
        elif text.startswith("nan:0x"):
            payload = big_parse(text[6:], 16)
            need(big_bits(payload) <= precision and big_bits(payload) > 0, "invalid NaN payload")
            for index in range(len(payload) - 1, -1, -1):
                fraction = (fraction << 15) | payload[index]
        else:
            need(text == "inf", "invalid float")
    else:
        base = 10
        exp_base = 10
        marker = "e"
        if text.startswith("0x"):
            base = 16
            exp_base = 2
            marker = "p"
            text = text[2:]
        pos = text.find(marker)
        power = 0
        if pos >= 0:
            power = i32(text[pos + 1:])
            text = text[:pos]
        point = text.find(".")
        if point >= 0:
            power -= (len(text) - point - 1) * (4 if base == 16 else 1)
            text = text[:point] + text[point + 1:]
        n = big_parse(text, base)
        d: list[i32] = [1]
        if big_bits(n) != 0:
            # Compiler literals have bounded exponents; refuse pathological WAT.
            need(power >= -4096 and power <= 4096, "floating exponent out of bootstrap range")
            for index in range(abs(power)):
                if power < 0:
                    big_mul(d, exp_base, 0)
                else:
                    big_mul(n, exp_base, 0)
            e = big_bits(n) - big_bits(d)
            if big_cmp(big_shift(n, max(-e, 0)), big_shift(d, max(e, 0))) < 0:
                e -= 1
            target = max(e, 1 - bias)
            fraction = rounded_ratio(n, d, precision - target)
            if fraction == i64(1) << (precision + 1):
                fraction >>= 1
                target += 1
            need(target <= bias, "floating constant out of range")
            if fraction >= i64(1) << precision:
                exponent = target + bias
                fraction -= i64(1) << precision
    # At most 63 bits before setting the sign bit in the final byte.
    bits = fraction | (i64(exponent) << precision)
    out: list[i32] = []
    for index in range(width // 8):
        out.append(i32(bits & 255))
        bits >>= 8
    if negative:
        out[-1] |= 128
    return out


def integer_constant(out: list[i32], text: str, width: i32) -> None:
    text = text.replace("_", "")
    negative = text.startswith("-")
    if negative or text.startswith("+"):
        text = text[1:]
    base = 10
    if text.startswith("0x"):
        base = 16
        text = text[2:]
    digits = big_parse(text, base)
    limit = big_shift([1], width - 1 if negative else width)
    cmp = big_cmp(digits, limit)
    need(cmp <= 0 if negative else cmp < 0, "integer constant out of range")
    # Build a signed i64 without overflowing even in the CPython shim. Treat the
    # top bit separately, so unsigned Wasm literals preserve their bit patterns.
    value: i64 = 0
    for bit in range(width - 1, -1, -1):
        limb = bit // 15
        present = limb < len(digits) and (digits[limb] & (1 << (bit % 15))) != 0
        if present:
            if bit == 63:
                value = -9223372036854775808
            else:
                value |= i64(1) << bit
    if negative and value != -9223372036854775808:
        value = -value
    elif not negative and width == 32 and value >= 2147483648:
        value -= 4294967296
    sleb(out, value)


class Symbols:
    kind: str
    names: dict[str, i32]
    count: i32

    def __init__(self, kind: str) -> None:
        self.kind = kind
        self.names = {}
        self.count = 0

    def add(self, name: str) -> i32:
        index = self.count
        self.count += 1
        if name != "":
            need(name not in self.names, "duplicate " + self.kind + " " + name)
            self.names[name] = index
        return index

    def get(self, name: str) -> i32:
        if name.startswith("$"):
            need(name in self.names, "unknown " + self.kind + " " + name)
            return self.names[name]
        index = unsigned(name)
        need(index < self.count, "index out of range: " + self.kind)
        return i32(index)


class Signature:
    params: list[list[i32]]
    results: list[list[i32]]

    def __init__(self, params: list[list[i32]], results: list[list[i32]]) -> None:
        self.params = params
        self.results = results

    def data(self) -> list[i32]:
        out: list[i32] = [96]
        out.extend(vector(self.params))
        out.extend(vector(self.results))
        return out


class Encoder:
    forms: list[Node]
    spaces: dict[str, Symbols]
    fields: dict[i32, Symbols]
    types: list[list[i32]]
    signatures: dict[i32, Signature]
    implicit: dict[str, i32]
    exports: list[list[i32]]
    export_names: dict[str, bool]
    heaps: dict[str, i32]
    simple: dict[str, i32]
    memory_ops: dict[str, i32]
    alignments: list[i32]
    gc: dict[str, i32]
    primitives: dict[str, i32]
    index_kinds: dict[str, str]
    index_codes: dict[str, i32]
    control_codes: dict[str, i32]
    catch_codes: dict[str, i32]
    saturating: list[str]

    def __init__(self, root: Node) -> None:
        self.forms = root.parts()
        self.spaces = {}
        self.fields = {}
        self.types = []
        self.signatures = {}
        self.implicit = {}
        self.exports = []
        self.export_names = {}
        # These tables are immutable for an encoding. Constructing them inside
        # value()/instruction()/control() allocates on every visited WAT node.
        self.primitives = {"i32": 127, "i64": 126, "f32": 125, "f64": 124}
        self.index_kinds = {"local.get": "local", "local.set": "local", "local.tee": "local",
            "global.get": "global", "global.set": "global", "call": "func", "call_ref": "type", "ref.func": "func", "throw": "tag"}
        self.index_codes = {"local.get": 32, "local.set": 33, "local.tee": 34, "global.get": 35,
            "global.set": 36, "call": 16, "call_ref": 20, "ref.func": 210, "throw": 8}
        self.control_codes = {"block": 2, "loop": 3, "if": 4, "try_table": 31}
        self.catch_codes = {"catch": 0, "catch_ref": 1, "catch_all": 2, "catch_all_ref": 3}
        self.saturating = ["i32.trunc_sat_f32_s", "i32.trunc_sat_f32_u", "i32.trunc_sat_f64_s", "i32.trunc_sat_f64_u",
                           "i64.trunc_sat_f32_s", "i64.trunc_sat_f32_u", "i64.trunc_sat_f64_s", "i64.trunc_sat_f64_u"]
        self.heaps = {"func": 112, "extern": 111, "any": 110, "eq": 109, "i31": 108, "struct": 107,
                      "array": 106, "none": 113, "noextern": 114, "nofunc": 115, "exn": 105, "noexn": 116}
        self.simple = {"unreachable": 0, "nop": 1, "return": 15, "drop": 26, "select": 27,
                       "ref.is_null": 209, "ref.eq": 211, "ref.as_non_null": 212}
        self.memory_ops = {}
        self.alignments = [2, 3, 2, 3, 0, 0, 1, 1, 0, 0, 1, 1, 2, 2, 2, 3, 2, 3, 0, 1, 0, 1, 2]
        self.gc = {"struct.new": 0, "struct.new_default": 1, "struct.get": 2, "struct.get_s": 3,
                   "struct.get_u": 4, "struct.set": 5, "array.new": 6, "array.new_default": 7,
                   "array.new_fixed": 8, "array.new_data": 9, "array.get": 11, "array.get_s": 12,
                   "array.get_u": 13, "array.set": 14, "array.fill": 16, "array.copy": 17, "array.init_data": 18}
        for kind in ["type", "func", "memory", "global", "tag", "data"]:
            self.spaces[kind] = Symbols(kind)
        numeric = """i32.eqz i32.eq i32.ne i32.lt_s i32.lt_u i32.gt_s i32.gt_u i32.le_s i32.le_u i32.ge_s i32.ge_u
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
i32.extend8_s i32.extend16_s i64.extend8_s i64.extend16_s i64.extend32_s""".split()
        for index in range(len(numeric)):
            self.simple[numeric[index]] = index + 69
        memory = """i32.load i64.load f32.load f64.load i32.load8_s i32.load8_u i32.load16_s i32.load16_u
i64.load8_s i64.load8_u i64.load16_s i64.load16_u i64.load32_s i64.load32_u
i32.store i64.store f32.store f64.store i32.store8 i32.store16 i64.store8 i64.store16 i64.store32""".split()
        for index in range(len(memory)):
            self.memory_ops[memory[index]] = index + 40

    def lookup(self, kind: str, node: Node) -> i32:
        need(node.kind == 1, "expected " + kind + " index")
        return self.spaces[kind].get(node.text)

    def heap(self, node: Node) -> list[i32]:
        need(node.kind == 1, "expected heap type")
        out: list[i32] = []
        if node.text in self.heaps:
            out.append(self.heaps[node.text])
        else:
            sleb(out, self.lookup("type", node))
        return out

    def value(self, node: Node) -> list[i32]:
        if node.kind == 1:
            if node.text in self.primitives:
                return [self.primitives[node.text]]
            if node.text.endswith("ref") and node.text[:-3] in self.heaps:
                return [self.heaps[node.text[:-3]]]
        need(node.head() == "ref", "unsupported value type")
        nullable = len(node.items) == 3 and node.items[1].text == "null"
        need(len(node.items) == (3 if nullable else 2), "invalid reference type")
        heap = self.heap(node.items[-1])
        if nullable and node.items[-1].text in self.heaps:
            return heap
        out: list[i32] = [99 if nullable else 100]
        out.extend(heap)
        return out

    def field_type(self, node: Node) -> list[i32]:
        mutable = node.head() == "mut"
        if mutable:
            need(len(node.items) == 2, "invalid mutable type")
            node = node.items[1]
        out: list[i32] = []
        if node.text == "i8":
            out.append(120)
        elif node.text == "i16":
            out.append(119)
        else:
            out.extend(self.value(node))
        out.append(1 if mutable else 0)
        return out

    def bindings(self, forms: list[Node], kind: str, space: Symbols) -> list[list[i32]]:
        out: list[list[i32]] = []
        for form in forms:
            if form.head() == kind:
                parts = form.parts()
                name = form.name()
                need(len(parts) > 0 and (name == "" or len(parts) == 1), "invalid binding")
                for part in parts:
                    space.add(name)
                    out.append(self.value(part))
        return out

    def signature(self, forms: list[Node]) -> Signature:
        return Signature(self.bindings(forms, "param", Symbols("parameter")), self.bindings(forms, "result", Symbols("result")))

    def intern(self, sig: Signature) -> i32:
        data = sig.data()
        key = byte_key(data)
        if key in self.implicit:
            return self.implicit[key]
        index = self.spaces["type"].add("")
        self.implicit[key] = index
        self.signatures[index] = sig
        self.types.append(data)
        return index

    def type_use(self, forms: list[Node]) -> i32:
        explicit = -1
        inline = False
        for form in forms:
            if form.head() == "type":
                need(explicit == -1 and len(form.items) == 2, "invalid type use")
                explicit = self.lookup("type", form.items[1])
            elif form.head() == "param" or form.head() == "result":
                inline = True
        if explicit == -1:
            return self.intern(self.signature(forms))
        need(explicit in self.signatures, "expected function type")
        if inline:
            need(self.signature(forms).data() == self.signatures[explicit].data(), "inline signature mismatch")
        return explicit

    def composite(self, form: Node) -> list[i32]:
        parts = form.parts()
        need(len(parts) == 1, "invalid type definition")
        body = parts[0]
        out: list[i32] = []
        if body.head() == "sub":
            parts = body.items[1:]
            final = len(parts) > 0 and parts[0].text == "final"
            if final:
                parts = parts[1:]
            need(len(parts) == 1 or len(parts) == 2, "invalid subtype")
            out.append(79 if final else 80)
            uleb(out, len(parts) - 1)
            if len(parts) == 2:
                uleb(out, self.lookup("type", parts[0]))
            body = parts[-1]
        kind = body.head()
        if kind == "func":
            for part in body.items[1:]:
                need(part.head() == "param" or part.head() == "result", "invalid function type")
            sig = self.signature(body.items[1:])
            self.signatures[form.index] = sig
            out.extend(sig.data())
        elif kind == "struct":
            fields = Symbols("field")
            out.append(95)
            uleb(out, len(body.items) - 1)
            for member in body.items[1:]:
                need(member.head() == "field", "expected field")
                fields.add(member.name())
                values = member.parts()
                need(len(values) == 1, "expected one field type")
                out.extend(self.field_type(values[0]))
            self.fields[form.index] = fields
        elif kind == "array":
            need(len(body.items) == 2, "expected array element type")
            out.append(94)
            out.extend(self.field_type(body.items[1]))
        else:
            raise ValueError("unsupported composite type " + kind)
        return out

    def collect(self) -> None:
        imports: list[Node] = []
        definitions: list[Node] = []
        groups: list[Node] = []
        for form in self.forms:
            kind = form.head()
            if kind == "type" or kind == "rec":
                members: list[Node] = [form]
                if kind == "rec":
                    members = form.items[1:]
                need(len(members) > 0, "empty recursion group")
                for member in members:
                    need(member.head() == "type", "expected type")
                    member.index = self.spaces["type"].add(member.name())
                groups.append(form)
            elif kind == "import":
                need(len(form.items) == 4 and form.items[1].kind == 2 and form.items[2].kind == 2, "invalid import")
                imports.append(form.items[3])
            elif kind in ["func", "memory", "global", "tag", "data"]:
                definitions.append(form)
            else:
                need(kind in ["export", "elem", "start"], "unsupported module field " + kind)
        for group in groups:
            data: list[i32] = []
            if group.head() == "rec":
                data.append(78)
                uleb(data, len(group.items) - 1)
                for member in group.items[1:]:
                    data.extend(self.composite(member))
            else:
                data.extend(self.composite(group))
            self.types.append(data)
        for form in imports:
            need(form.head() in ["func", "memory", "global", "tag"], "unsupported import")
        for form in imports + definitions:
            form.index = self.spaces[form.head()].add(form.name())
        for form in imports + definitions:
            if form.head() == "func" or form.head() == "tag":
                form.signature = self.type_use(form.parts())

    def export(self, name: Node, kind: str, index: i32) -> None:
        kinds: dict[str, i32] = {"func": 0, "memory": 2, "global": 3, "tag": 4}
        need(name.kind == 2 and kind in kinds, "invalid export")
        key = byte_key(name.data)
        need(key not in self.export_names, "duplicate export")
        self.export_names[key] = True
        out: list[i32] = []
        blob(out, name.data)
        out.append(kinds[kind])
        uleb(out, index)
        self.exports.append(out)

    def parts(self, form: Node) -> list[Node]:
        out: list[Node] = []
        for part in form.parts():
            if part.head() == "export":
                need(len(part.items) == 2, "invalid inline export")
                self.export(part.items[1], form.head(), form.index)
            else:
                out.append(part)
        return out

    def memory(self, parts: list[Node]) -> list[i32]:
        need(len(parts) == 1 or len(parts) == 2, "expected memory limits")
        out: list[i32] = [len(parts) - 1]
        previous: i64 = 0
        for part in parts:
            value = unsigned(part.text)
            need(value >= previous and value <= 65536, "invalid memory limits")
            uleb(out, value)
            previous = value
        return out

    def function(self, form: Node, parts: list[Node]) -> list[i32]:
        locals = Symbols("local")
        params = self.bindings(parts, "param", locals)
        if len(params) == 0:
            for value in self.signatures[form.signature].params:
                locals.add("")
        types = self.bindings(parts, "local", locals)
        grouped: list[list[i32]] = []
        index = 0
        while index < len(types):
            last = index + 1
            while last < len(types) and types[last] == types[index]:
                last += 1
            group: list[i32] = []
            uleb(group, last - index)
            group.extend(types[index])
            grouped.append(group)
            index = last
        body = vector(grouped)
        code = Code(self, locals)
        code.labels.append("")
        for part in parts:
            if part.head() not in ["param", "result", "type", "local"]:
                code.instruction(body, part)
        body.append(11)
        out: list[i32] = []
        blob(out, body)
        return out

    def encode(self) -> list[i32]:
        self.collect()
        entries: dict[i32, list[list[i32]]] = {}
        for section_id in [2, 3, 5, 13, 6, 9, 10, 11]:
            entries[section_id] = []
        start: list[i32] = []
        for form in self.forms:
            kind = form.head()
            if kind == "type" or kind == "rec":
                continue
            out: list[i32] = []
            if kind == "import":
                desc = form.items[3]
                parts = self.parts(desc)
                blob(out, form.items[1].data)
                blob(out, form.items[2].data)
                if desc.head() == "func":
                    for part in parts:
                        need(part.head() in ["param", "result", "type"], "invalid function import")
                    out.append(0)
                    uleb(out, desc.signature)
                elif desc.head() == "memory":
                    out.append(2)
                    out.extend(self.memory(parts))
                elif desc.head() == "global":
                    need(len(parts) == 1, "invalid global import")
                    out.append(3)
                    out.extend(self.field_type(parts[0]))
                else:
                    for part in parts:
                        need(part.head() in ["param", "type"], "invalid tag import")
                    out.extend([4, 0])
                    uleb(out, desc.signature)
                entries[2].append(out)
                continue
            parts = form.items[1:]
            if kind in self.spaces:
                parts = self.parts(form)
            if kind == "func":
                uleb(out, form.signature)
                entries[3].append(out)
                entries[10].append(self.function(form, parts))
            elif kind == "memory":
                entries[5].append(self.memory(parts))
            elif kind == "tag":
                for part in parts:
                    need(part.head() in ["param", "type"], "invalid tag")
                out.append(0)
                uleb(out, form.signature)
                entries[13].append(out)
            elif kind == "global":
                need(len(parts) >= 2, "missing global initializer")
                out.extend(self.field_type(parts[0]))
                code = Code(self, Symbols("local"))
                code.sequence(out, parts[1:])
                out.append(11)
                entries[6].append(out)
            elif kind == "data":
                data: list[i32] = []
                for part in parts:
                    need(part.kind == 2, "only passive data segments are supported")
                    data.extend(part.data)
                out.append(1)
                blob(out, data)
                entries[11].append(out)
            elif kind == "elem":
                need(len(parts) >= 2 and parts[0].text == "declare" and parts[1].text == "func", "unsupported element segment")
                out.extend([3, 0])
                uleb(out, len(parts) - 2)
                for part in parts[2:]:
                    uleb(out, self.lookup("func", part))
                entries[9].append(out)
            elif kind == "export":
                need(len(parts) == 2 and len(parts[1].items) == 2, "invalid export")
                self.export(parts[0], parts[1].head(), self.lookup(parts[1].head(), parts[1].items[1]))
            elif kind == "start":
                need(len(start) == 0 and len(parts) == 1, "invalid start")
                uleb(start, self.lookup("func", parts[0]))
        entries[1] = self.types
        entries[7] = self.exports
        binary: list[i32] = [0, 97, 115, 109, 1, 0, 0, 0]
        for section_id in [1, 2, 3, 5, 13, 6, 7]:
            if len(entries[section_id]) > 0:
                section(binary, section_id, vector(entries[section_id]))
        if len(start) > 0:
            section(binary, 8, start)
        if len(entries[9]) > 0:
            section(binary, 9, vector(entries[9]))
        if len(entries[11]) > 0:
            count: list[i32] = []
            uleb(count, len(entries[11]))
            section(binary, 12, count)
        for section_id in [10, 11]:
            if len(entries[section_id]) > 0:
                section(binary, section_id, vector(entries[section_id]))
        return binary


class Code:
    module: Encoder
    locals: Symbols
    labels: list[str]

    def __init__(self, module: Encoder, locals: Symbols) -> None:
        self.module = module
        self.locals = locals
        self.labels = []

    def label(self, out: list[i32], node: Node) -> None:
        need(node.kind == 1, "expected branch target")
        if node.named():
            for index in range(len(self.labels) - 1, -1, -1):
                if self.labels[index] == node.text:
                    uleb(out, len(self.labels) - index - 1)
                    return
            raise ValueError("unknown label " + node.text)
        depth = unsigned(node.text)
        need(depth < len(self.labels), "label depth out of range")
        uleb(out, depth)

    def sequence(self, out: list[i32], nodes: list[Node]) -> None:
        for node in nodes:
            self.instruction(out, node)

    def control(self, out: list[i32], node: Node) -> None:
        op = node.head()
        parts = node.parts()
        declarations: list[Node] = []
        index = 0
        explicit = False
        while index < len(parts) and parts[index].head() in ["type", "param", "result"]:
            declarations.append(parts[index])
            if parts[index].head() == "type":
                explicit = True
            index += 1
        parts = parts[index:]
        sig = self.module.signature(declarations)
        block_type: list[i32] = []
        if explicit or len(sig.params) > 0 or len(sig.results) > 1:
            sleb(block_type, self.module.type_use(declarations))
        elif len(sig.results) == 1:
            block_type.extend(sig.results[0])
        else:
            block_type.append(64)
        if op == "if":
            index = 0
            while index < len(parts) and parts[index].head() != "then":
                self.instruction(out, parts[index])
                index += 1
            parts = parts[index:]
            need(len(parts) == 1 or (len(parts) == 2 and parts[1].head() == "else"), "invalid if arms")
        catches: list[list[i32]] = []
        if op == "try_table":
            index = 0
            while index < len(parts) and parts[index].head() in self.module.catch_codes:
                caught = parts[index]
                kind = self.module.catch_codes[caught.head()]
                need(len(caught.items) == (3 if kind < 2 else 2), "invalid catch")
                data: list[i32] = [kind]
                if kind < 2:
                    uleb(data, self.module.lookup("tag", caught.items[1]))
                self.label(data, caught.items[-1])
                catches.append(data)
                index += 1
            parts = parts[index:]
        self.labels.append(node.name())
        out.append(self.module.control_codes[op])
        out.extend(block_type)
        if op == "if":
            self.sequence(out, parts[0].items[1:])
            if len(parts) == 2:
                out.append(5)
                self.sequence(out, parts[1].items[1:])
        else:
            if op == "try_table":
                out.extend(vector(catches))
            self.sequence(out, parts)
        self.labels.pop()
        out.append(11)

    def instruction(self, out: list[i32], node: Node) -> None:
        need(node.kind == 0 and len(node.items) > 0, "expected folded instruction")
        op = node.head()
        args = node.items[1:]
        m = self.module
        if op in m.control_codes:
            self.control(out, node)
        elif op in m.simple:
            self.sequence(out, args)
            out.append(m.simple[op])
        elif op in ["memory.size", "memory.grow"]:
            memory = "0"
            if len(args) > 0 and args[0].kind == 1:
                memory = args[0].text
                args = args[1:]
            self.sequence(out, args)
            out.append(63 if op == "memory.size" else 64)
            uleb(out, m.spaces["memory"].get(memory))
        elif op == "array.len":
            self.sequence(out, args)
            out.extend([251, 15])
        elif op in ["i32.const", "i64.const", "f32.const", "f64.const"]:
            need(len(args) == 1 and args[0].kind == 1, "expected numeric constant")
            width = 32 if op[1:3] == "32" else 64
            if op.startswith("i"):
                out.append(65 if width == 32 else 66)
                integer_constant(out, args[0].text, width)
            else:
                out.append(67 if width == 32 else 68)
                out.extend(float_bytes(args[0].text, width))
        elif op in m.index_codes:
            need(len(args) > 0, "expected index")
            index = 0
            if m.index_kinds[op] == "local":
                index = self.locals.get(args[0].text)
            else:
                index = m.lookup(m.index_kinds[op], args[0])
            self.sequence(out, args[1:])
            out.append(m.index_codes[op])
            uleb(out, index)
        elif op == "br" or op == "br_if":
            need(len(args) > 0, "expected branch target")
            self.sequence(out, args[1:])
            out.append(12 if op == "br" else 13)
            self.label(out, args[0])
        elif op == "ref.null":
            need(len(args) == 1, "expected heap type")
            out.append(208)
            out.extend(m.heap(args[0]))
        elif op == "ref.test" or op == "ref.cast":
            need(len(args) > 0 and args[0].head() == "ref", "expected reference type")
            m.value(args[0])
            self.sequence(out, args[1:])
            code = 20 if op == "ref.test" else 22
            if len(args[0].items) == 3:
                code += 1
            out.extend([251, code])
            out.extend(m.heap(args[0].items[-1]))
        elif op in m.gc:
            need(len(args) > 0, "expected GC type")
            code = m.gc[op]
            index = m.lookup("type", args[0])
            count = 1
            immediate: list[i32] = []
            uleb(immediate, index)
            if (code >= 2 and code <= 5) or code == 8 or code == 9 or code == 17 or code == 18:
                count = 2
                need(len(args) > 1, "missing GC immediate")
                if code >= 2 and code <= 5:
                    need(index in m.fields, "expected struct type")
                    uleb(immediate, m.fields[index].get(args[1].text))
                elif code == 9 or code == 18:
                    uleb(immediate, m.lookup("data", args[1]))
                elif code == 8:
                    uleb(immediate, unsigned(args[1].text))
                else:
                    uleb(immediate, m.lookup("type", args[1]))
            self.sequence(out, args[count:])
            out.extend([251, code])
            out.extend(immediate)
        elif op in m.memory_ops:
            code = m.memory_ops[op]
            alignment = m.alignments[code - 40]
            offset: i64 = 0
            seen: list[str] = []
            index = 0
            while index < len(args) and args[index].kind == 1:
                pair = args[index].text.split("=")
                need(len(pair) == 2 and pair[0] in ["offset", "align"] and pair[0] not in seen, "invalid memory argument")
                seen.append(pair[0])
                number = unsigned(pair[1])
                if pair[0] == "offset":
                    offset = number
                else:
                    need(number > 0 and (number & (number - 1)) == 0 and number <= i64(1) << alignment, "invalid memory alignment")
                    alignment = 0
                    while number > 1:
                        number >>= 1
                        alignment += 1
                index += 1
            self.sequence(out, args[index:])
            out.append(code)
            uleb(out, alignment)
            uleb(out, offset)
        elif ".trunc_sat_" in op:
            need(op in m.saturating, "unsupported saturating conversion")
            self.sequence(out, args)
            out.extend([252, m.saturating.index(op)])
        else:
            raise ValueError("unsupported instruction " + op + " at WAT line " + str(node.line))


def encode(data: list[i32]) -> list[i32]:
    return Encoder(Reader(data).parse()).encode()


if __name__ == "__main__":
    write_bytes(encode(read_bytes()))
