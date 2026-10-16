"""WAT names, literal data and Unicode tables, implemented in Snek.

Unicode properties come from the compiler's own embedded string runtime, not
host Python or an external UCD installation. Surrogates cannot inhabit a Snek
string; their table entries are the explicit Unicode Cs defaults.
"""
from __future__ import annotations
from snek import i32
from bootstrap.declarations import DeclType, Declarations


def emit_mangle(ty: DeclType) -> str:
    kind = ty.kind
    if kind == "None":
        return "none"
    if kind == "class":
        return ty.name
    if kind == "__Buf":
        return "buf"
    if kind == "TracebackType":
        return "traceback<>"
    if kind == "Stream":
        return "stream." + ty.name
    if kind == "optional":
        return emit_mangle(ty.args[0]) + "?"
    parts: list[str] = []
    for arg in ty.args:
        parts.append(emit_mangle(arg))
    if kind == "Callable":
        return "callable<" + "~".join(parts[:-1]) + ":" + parts[-1] + ">"
    if kind == "Env":
        return "env<>" if ty.name == "" else "env<" + "~".join(parts) + ">.scope"
    if kind == "Cell":
        return "cell<" + parts[0] + ">"
    if kind == "Iterator" or kind == "Coroutine":
        return ("iter" if kind == "Iterator" else "coro") + "<" + parts[0] + ">"
    if kind == "view":
        return parts[0] + "." + ty.name + ".view"
    if kind in ("list", "tuple", "dict", "type"):
        return kind + "<" + "~".join(parts) + ">"
    if kind in ("i32", "i64", "f32", "f64", "bool", "str"):
        return kind
    raise ValueError("unknown emitter type: " + ty.display())


def emit_utf8(text: str) -> list[i32]:
    out: list[i32] = []
    for ch in text:
        cp = ord(ch)
        if cp < 128:
            out.append(cp)
        elif cp < 2048:
            out.append(192 | (cp >> 6))
            out.append(128 | (cp & 63))
        elif cp < 65536:
            if cp >= 0xD800 and cp < 0xE000:
                raise ValueError("surrogate in WAT literal")
            out.append(224 | (cp >> 12))
            out.append(128 | ((cp >> 6) & 63))
            out.append(128 | (cp & 63))
        else:
            out.append(240 | (cp >> 18))
            out.append(128 | ((cp >> 12) & 63))
            out.append(128 | ((cp >> 6) & 63))
            out.append(128 | (cp & 63))
    return out


def emit_wat_string(data: list[i32]) -> str:
    out = ['"']
    digits = "0123456789abcdef"
    for byte in data:
        if byte < 0 or byte > 255:
            raise ValueError("WAT byte outside 0..255")
        if byte >= 32 and byte < 127 and byte != 34 and byte != 92:
            out.append(chr(byte))
        else:
            out.append("\\" + digits[byte >> 4] + digits[byte & 15])
    out.append('"')
    return "".join(out)


def emit_words(values: list[i32], size: i32 = 4) -> list[i32]:
    if size != 2 and size != 4:
        raise ValueError("Unicode table words must have size 2 or 4")
    out: list[i32] = []
    for value in values:
        for shift in range(size):
            out.append((value >> (shift * 8)) & 255)
    return out


class EmitLiterals:
    index: dict[str, i32]
    texts: list[str]

    def __init__(self) -> None:
        self.index = {}
        self.texts = []

    def lit(self, text: str) -> str:
        if text not in self.index:
            self.index[text] = len(self.texts)
            self.texts.append(text)
        return "(call $lit." + str(self.index[text]) + ")"

    def expand(self, text: str) -> str:
        # Match the runtime's literal marker, including escaped quotes. As in
        # the reference emitter, marker contents are text, not decoded escapes.
        out: list[str] = []
        start = 0
        while True:
            mark = text.find('(str.lit "', start)
            if mark < 0:
                out.append(text[start:])
                return "".join(out)
            pos = mark + 10
            end = pos
            valid = True
            while end < len(text):
                if text[end] == "\\":
                    if end + 1 >= len(text) or text[end + 1] == "\n":
                        valid = False
                        break
                    end += 2
                elif text[end] == '"':
                    break
                else:
                    end += 1
            if not valid or end + 1 >= len(text) or text[end:end + 2] != '\")':
                out.append(text[start:pos])
                start = pos
            else:
                out.append(text[start:mark])
                out.append(self.lit(text[pos:end]))
                start = end + 2

    def functions(self) -> str:
        out: list[str] = []
        for i in range(len(self.texts)):
            text = self.texts[i]
            data = emit_utf8(text)
            out.append(
                f"  (data $lit.{i} {emit_wat_string(data)})\n"
                f"  (global $lit.{i}.cache (mut (ref null $str)) (ref.null $str))\n"
                f"  (func $lit.{i} (result (ref $str))\n"
                f"    (if (ref.is_null (global.get $lit.{i}.cache))\n"
                f"      (then (global.set $lit.{i}.cache\n"
                f"        (call $rt.mkstr (array.new_data $u8s $lit.{i} (i32.const 0) (i32.const {len(data)})) (i32.const {len(text)})))))\n"
                f"    (ref.as_non_null (global.get $lit.{i}.cache)))")
        return "\n".join(out)


class EmitTypes:
    lists: dict[str, DeclType]
    tuples: dict[str, DeclType]
    dicts: dict[str, DeclType]
    views: dict[str, DeclType]
    callables: dict[str, DeclType]
    cells: dict[str, DeclType]
    envs: dict[str, DeclType]
    machines: dict[str, DeclType]

    def __init__(self) -> None:
        self.lists = {}
        self.tuples = {}
        self.dicts = {}
        self.views = {}
        self.callables = {}
        self.cells = {}
        self.envs = {}
        self.machines = {}

    def heap(self, ty: DeclType) -> str:
        if ty.kind == "optional": return self.heap(ty.args[0])
        kind = ty.kind
        name = emit_mangle(ty)
        if kind == "Stream": return "$io.stream"
        if kind in ("Callable", "Cell", "Env", "Iterator", "Coroutine"):
            if kind == "Env" and ty.name == "": return "$env<>"
            registry = self.callables
            if kind == "Cell": registry = self.cells
            elif kind == "Env": registry = self.envs
            elif kind == "Iterator" or kind == "Coroutine": registry = self.machines
            if name not in registry:
                registry[name] = ty
                for arg in ty.args:
                    if arg.kind != "None": self.storage(arg)
            return "$" + name
        if kind == "list":
            name = emit_mangle(ty.args[0])
            self.lists[name] = ty
            return "$list." + name
        if kind == "tuple": self.tuples[name] = ty
        elif kind == "dict": self.dicts[name] = ty
        elif kind == "view": self.views[name] = ty
        elif kind == "type": return "$" + ty.args[0].name + ".vt"
        elif kind not in ("str", "__Buf", "class", "TracebackType"):
            raise ValueError("no heap type for " + ty.display())
        return "$" + name

    def valtype(self, ty: DeclType) -> str:
        if ty.kind == "bool": return "i32"
        if ty.kind in ("i32", "i64", "f32", "f64"): return ty.kind
        if ty.kind == "optional": return "(ref null " + self.heap(ty) + ")"
        return "(ref " + self.heap(ty) + ")"

    def storage(self, ty: DeclType) -> str:
        return "(ref null " + self.heap(ty) + ")" if ty.reference() else self.valtype(ty)

    def array_elem(self, ty: DeclType) -> str:
        return "i8" if ty.kind == "bool" else self.storage(ty)

    def close(self) -> None:
        while True:
            before = len(self.lists) + len(self.tuples) + len(self.dicts) + len(self.views)
            for ty in list(self.lists.values()): self.array_elem(ty.args[0])
            for ty in list(self.tuples.values()):
                for arg in ty.args: self.valtype(arg)
            for ty in list(self.dicts.values()):
                for arg in ty.args: self.valtype(arg)
            for ty in list(self.views.values()):
                mapping = ty.args[0]
                self.heap(mapping)
                if ty.name == "items":
                    self.heap(DeclType("tuple").add(mapping.args[0]).add(mapping.args[1]))
                else:
                    self.valtype(mapping.args[1] if ty.name == "values" else mapping.args[0])
            after = len(self.lists) + len(self.tuples) + len(self.dicts) + len(self.views)
            if before == after: return

    def collection_types(self) -> list[str]:
        self.close()
        out: list[str] = []
        for name, ty in self.tuples.items():
            fields: list[str] = []
            for arg in ty.args: fields.append(f"(field {self.valtype(arg)})")
            out.append(f"    (type ${name} (struct {' '.join(fields)}))")
        for name, ty in self.dicts.items():
            out.extend([
                f"    (type ${name}.entry (struct (field $key {self.valtype(ty.args[0])})"
                f" (field $value (mut {self.valtype(ty.args[1])}))))",
                f"    (type ${name}.entries (array (mut (ref null ${name}.entry))))",
                f"    (type ${name} (struct (field $len (mut i32)) (field $used (mut i32))"
                f" (field $slots (mut (ref $u32s))) (field $entries (mut (ref ${name}.entries))) (field $iters (mut i32))))",
            ])
        for name, ty in self.views.items():
            out.append(f"    (type ${name} (struct (field $dict (ref {self.heap(ty.args[0])}))))")
        return out

    def closure_types(self) -> list[str]:
        lines = ["    (type $env<> (sub (struct)))"]
        for ty in self.callables.values():
            heap = self.heap(ty)
            args = ""
            for arg in ty.args[:-1]: args += f" (param {self.valtype(arg)})"
            ret = ty.args[-1]
            result = f" (result {self.valtype(ret)})" if ret.kind != "None" else ""
            lines.extend([f"    (type {heap}.sig (func (param (ref $env<>)){args}{result}))",
                          f"    (type {heap} (struct (field $fn (ref {heap}.sig)) (field $env (ref $env<>))))"])
        for ty in self.cells.values():
            lines.append(f"    (type {self.heap(ty)} (struct (field $bound (mut i32)) (field $value (mut {self.storage(ty.args[0])}))))")
        for ty in self.envs.values():
            fields = ""
            for arg in ty.args: fields += f" (field {self.valtype(arg)})"
            lines.append(f"    (type {self.heap(ty)} (sub $env<> (struct{fields})))")
        return lines

    def class_types(self, decl: Declarations) -> list[str]:
        out: list[str] = []
        for name in decl.class_order:
            cls = decl.classes[name]
            # Closed-world finality, not the source @final annotation.
            sub = "sub final"
            for candidate in decl.classes.values():
                if candidate.base == name: sub = "sub"
            sup_vt = " $" + cls.base + ".vt" if cls.base != "" else ""
            sup = " $" + cls.base if cls.base != "" else ""
            slots = ""
            for method in cls.methods.values():
                if method.decl == name:
                    params = ""
                    for param in method.sig.params: params += f" (param {self.valtype(param.ty)})"
                    ret = method.sig.ret
                    result = f" (result {self.valtype(ret)})" if ret.kind != "None" else ""
                    out.append(f"    (type ${name}.{method.name}.sig (func (param (ref ${name})){params}{result}))")
                slots += f" (field ${method.name} (ref ${method.decl}.{method.name}.sig))"
            out.append(f"    (type ${name}.vt ({sub}{sup_vt} (struct (field $__name__ (ref $str)){slots})))")
            fields = ""
            for field in cls.fields.values():
                value = self.valtype(field.ty)
                if not field.final: value = "(mut " + value + ")"
                fields += f" (field ${field.name} {value})"
            out.append(f"    (type ${name} ({sub}{sup} (struct (field $__vt__ (ref ${name}.vt)){fields})))")
        return out

    def vtable_globals(self, decl: Declarations) -> str:
        out: list[str] = []
        for name in decl.class_order:
            cls = decl.classes[name]
            data = emit_utf8(name)
            chars: list[str] = []
            for byte in data: chars.append(f"(i32.const {byte})")
            out.append(f"  (global ${name}.name (ref $str) (struct.new $str "
                       f"(array.new_fixed $u8s {len(data)} {' '.join(chars)}) (i32.const 0) (i32.const {len(data)}) "
                       f"(ref.null $u32s)))")
            refs = ""
            for method in cls.methods.values(): refs += f" (ref.func ${method.owner}.{method.name})"
            out.append(f"  (global ${name}.vt.inst (ref ${name}.vt) "
                       f"(struct.new ${name}.vt (global.get ${name}.name){refs}))")
        return "\n".join(out)


def emit_unicode_flags(cp: i32, mask: i32) -> i32:
    if cp >= 0xD800 and cp < 0xE000:
        return mask & 1
    ch = chr(cp)
    flags = 0
    if mask & 1 != 0 and not ch.isprintable(): flags |= 1
    if mask & 2 != 0 and ch.isspace(): flags |= 2
    if mask & 4 != 0 and ch.isalpha(): flags |= 4
    if mask & 8 != 0 and ch.isdecimal(): flags |= 8
    if mask & 16 != 0 and ch.isdigit(): flags |= 16
    if mask & 32 != 0 and ch.isnumeric(): flags |= 32
    if mask & 64 != 0 and ch.isupper(): flags |= 64
    if mask & 128 != 0 and ch.islower(): flags |= 128
    title = ch.istitle() and not ch.isupper() and not ch.islower()
    if mask & 256 != 0 and title: flags |= 256
    if mask & 512 != 0 and ch.isidentifier(): flags |= 512
    if mask & 1024 != 0 and ("a" + ch).isidentifier(): flags |= 1024
    if mask & 2048 != 0:
        if ch.isupper() or ch.islower() or title:
            if ("1" + ch + "Σ").lower()[-1] == "σ": flags |= 2048
        elif ("A" + ch + "Σ").lower()[-1] == "ς":
            flags |= 2048
    return flags


def emit_case(cp: i32, kind: i32) -> str:
    ch = chr(cp)
    if kind == 0: return ch.upper()
    if kind == 1: return ch.lower()
    if kind == 2: return ch.title()
    if kind == 3: return ch.casefold()
    raise ValueError("invalid Unicode case mapping")


def emit_codepoints(literals: list[str], cases: list[i32], extra: list[i32]) -> list[i32]:
    points: dict[i32, bool] = {}
    for cp in extra:
        if cp >= 128: points[cp] = True
    for text in literals:
        for ch in text:
            if ord(ch) >= 128: points[ord(ch)] = True
    pending = list(points)
    index = 0
    while index < len(pending):
        cp = pending[index]
        index += 1
        if cp >= 0xD800 and cp < 0xE000: continue
        for kind in cases:
            for ch in emit_case(cp, kind):
                value = ord(ch)
                if value >= 128 and value not in points:
                    points[value] = True
                    pending.append(value)
        if cp == 0x3A3 and 1 in cases and 0x3C2 not in points:
            points[0x3C2] = True
            pending.append(0x3C2)
    return sorted(pending)


class EmitUnicode:
    ascii_flags: list[i32]
    flags: list[i32]
    cases: list[list[i32]]
    specials: list[i32]
    zeros: list[i32]

    def __init__(self) -> None:
        self.ascii_flags = []
        self.flags = []
        self.cases = [[], [], [], []]
        self.specials = []
        self.zeros = []

    def flag(self, cp: i32, mask: i32) -> None:
        value = emit_unicode_flags(cp, mask)
        if len(self.flags) == 0 or self.flags[-1] != value:
            self.flags.append(cp)
            self.flags.append(value)

    def case(self, cp: i32, kind: i32) -> None:
        if cp >= 0xD800 and cp < 0xE000: return
        mapped = emit_case(cp, kind)
        if len(mapped) == 1 and ord(mapped) == cp: return
        runs = self.cases[kind]
        if len(mapped) > 1:
            runs.extend([cp, cp + 1, 0, (len(self.specials) << 2) | len(mapped)])
            for ch in mapped: self.specials.append(ord(ch))
            return
        delta = ord(mapped) - cp
        if len(runs) > 0:
            start = runs[-4]
            end = runs[-3]
            stride = runs[-2]
            if stride != 0 and runs[-1] == delta:
                if (stride == 1 and cp == end) or (stride == 2 and cp == end + 1):
                    runs[-3] = cp + 1
                    return
                if end - start == 1 and cp == start + 2:
                    runs[-3] = cp + 1
                    runs[-2] = 2
                    return
        runs.extend([cp, cp + 1, 1, delta])

    def build(self, literals: list[str], mask: i32, cases: list[i32], decimal: bool,
              dynamic: bool, extra: list[i32]) -> None:
        self.ascii_flags = []
        self.flags = []
        self.cases = [[], [], [], []]
        self.specials = []
        self.zeros = []
        if decimal: mask |= 8
        for cp in range(128): self.ascii_flags.append(emit_unicode_flags(cp, mask))
        points: list[i32] = []
        if not dynamic: points = emit_codepoints(literals, cases, extra)
        count = 0x110000 - 128 if dynamic else len(points)
        for index in range(count):
            cp = index + 128 if dynamic else points[index]
            self.flag(cp, mask)
        # Kind-major ordering is part of the table ABI: offsets refer to one
        # shared specials array. Retain it even if the caller orders kinds oddly.
        for kind in range(4):
            if kind in cases:
                for index in range(count):
                    cp = index + 128 if dynamic else points[index]
                    self.case(cp, kind)
        if decimal:
            for index in range(count):
                cp = index + 128 if dynamic else points[index]
                if cp >= 0xD800 and cp < 0xE000: continue
                ch = chr(cp)
                if ch.isdecimal():
                    zero = cp - i32(ch)
                    if len(self.zeros) == 0 or self.zeros[-1] != zero:
                        self.zeros.append(zero)

    def wat(self) -> str:
        cases: list[i32] = []
        for runs in self.cases: cases.append(len(runs) // 4)
        for runs in self.cases: cases.extend(runs)
        return "\n".join([
            f"  (data $uascii {emit_wat_string(emit_words(self.ascii_flags, 2))})",
            f"  (data $uflags {emit_wat_string(emit_words(self.flags))})",
            f"  (global $rt.uf.n i32 (i32.const {len(self.flags) // 2}))",
            f"  (data $ucases {emit_wat_string(emit_words(cases))})",
            f"  (global $rt.uc.n i32 (i32.const {len(cases)}))",
            f"  (data $uspecial {emit_wat_string(emit_words(self.specials))})",
            f"  (global $rt.usp.n i32 (i32.const {len(self.specials)}))",
            f"  (data $uzeros {emit_wat_string(emit_words(self.zeros))})",
            f"  (global $rt.uz.n i32 (i32.const {len(self.zeros)}))",
        ])
