"""Typed IR -> WebAssembly text (WAT) using WasmGC.

Layout decisions (see DESIGN.md):
  str          (struct (field $bytes (ref $u8s)) (field $hash (mut i32)))   UTF-8 bytes
  list[T]      (struct (field $len (mut i32)) (field $data (mut (ref $arr.T))))
               monomorphized per T; list[bool] uses a packed (array (mut i8))
  class C      (struct (field $__vt__ (ref $C.vt)) (field $f T) ...)
               single inheritance = WasmGC struct subtyping; vtables are structs
               of typed funcrefs, one immutable global instance per class
  T | None     (ref null $T); everything else is non-nullable
  locals       reference locals are stored nullable and read with ref.as_non_null
               (or ref.cast when narrowed); wasm-opt removes most of these

The whole program is known at compile time, so classes without subclasses are
emitted `final` and calls are devirtualized when only one implementation of a
method is reachable from the receiver's static type.
"""

from __future__ import annotations

import re
from pathlib import Path

from . import ir
from .types import (
    BOOL,
    F32,
    F64,
    I32,
    I64,
    NONE,
    STR,
    ClassInfo,
    ClassT,
    ListT,
    OptionalT,
    Prim,
    Type,
    strip_optional,
)

PRELUDE = Path(__file__).with_name("prelude.wat")

CONVERSIONS = {
    (I32, I64): "i64.extend_i32_s", (I32, F32): "f32.convert_i32_s", (I32, F64): "f64.convert_i32_s",
    (I64, I32): "i32.wrap_i64", (I64, F32): "f32.convert_i64_s", (I64, F64): "f64.convert_i64_s",
    (F32, I32): "i32.trunc_sat_f32_s", (F32, I64): "i64.trunc_sat_f32_s", (F32, F64): "f64.promote_f32",
    (F64, I32): "i32.trunc_sat_f64_s", (F64, I64): "i64.trunc_sat_f64_s", (F64, F32): "f32.demote_f64",
    (BOOL, I32): "", (BOOL, I64): "i64.extend_i32_u", (BOOL, F32): "f32.convert_i32_u",
    (BOOL, F64): "f64.convert_i32_u",
}

INT_CMP = {"eq": "eq", "ne": "ne", "lt": "lt_s", "le": "le_s", "gt": "gt_s", "ge": "ge_s"}
FLOAT_CMP = {"eq": "eq", "ne": "ne", "lt": "lt", "le": "le", "gt": "gt", "ge": "ge"}
SIMPLE_BIN = {"add": "add", "sub": "sub", "mul": "mul", "bitand": "and", "bitor": "or", "bitxor": "xor",
              "shl": "shl", "shr": "shr_s"}


def wat_string(data: bytes) -> str:
    out = []
    for b in data:
        c = chr(b)
        if 0x20 <= b < 0x7F and c not in '"\\':
            out.append(c)
        else:
            out.append(f"\\{b:02x}")
    return '"' + "".join(out) + '"'


def mangle(t: Type) -> str:
    if isinstance(t, Prim):
        return t.name
    if t == STR:
        return "str"
    if isinstance(t, ListT):
        return "list_" + mangle(t.elem)
    if isinstance(t, ClassT):
        return t.info.name
    if isinstance(t, OptionalT):
        return "opt_" + mangle(t.inner)
    raise AssertionError(t)


class FnState:
    def __init__(self, f: ir.Func, emitter: Emitter):
        self.f = f
        self.loops: list[tuple[str, str]] = []
        self.labels = 0
        self.temps: list[tuple[str, str]] = []
        self.nonnull = set(f.nonnull_locals) | {n for n, t in f.params if t.is_ref and not isinstance(t, OptionalT)}

    def label(self) -> int:
        self.labels += 1
        return self.labels

    def temp(self, wasm_type: str) -> str:
        name = f"$e.{len(self.temps)}"
        self.temps.append((name, wasm_type))
        return name


class Emitter:
    def __init__(self, module: ir.Module):
        self.m = module
        self.lit_index: dict[str, int] = {}
        self.lit_data = bytearray()
        self.lit_spans: list[tuple[int, int]] = []
        self.list_types: dict[str, ListT] = {}
        self.fn: FnState | None = None

    # ---- types -----------------------------------------------------------------

    def heap(self, t: Type) -> str:
        t = strip_optional(t)
        if t == STR:
            return "$str"
        if isinstance(t, ListT):
            m = mangle(t.elem)
            self.list_types.setdefault(m, t)
            return f"$list.{m}"
        if isinstance(t, ClassT):
            return f"${t.info.name}"
        raise AssertionError(f"no heap type for {t}")

    def valtype(self, t: Type) -> str:
        if isinstance(t, Prim):
            return "i32" if t == BOOL else t.name
        if isinstance(t, OptionalT):
            return f"(ref null {self.heap(t)})"
        return f"(ref {self.heap(t)})"

    def storage(self, t: Type) -> str:
        """Local variable storage: references are nullable so locals can be declared up front."""
        if t.is_ref:
            return f"(ref null {self.heap(t)})"
        return self.valtype(t)

    def array_elem(self, t: Type) -> str:
        if t == BOOL:
            return "i8"  # packed
        return self.storage(t)

    # ---- string literal pool ---------------------------------------------------

    def lit(self, text: str) -> str:
        if text not in self.lit_index:
            data = text.encode("utf-8")
            self.lit_index[text] = len(self.lit_spans)
            self.lit_spans.append((len(self.lit_data), len(data)))
            self.lit_data += data
        return f"(call $lit.{self.lit_index[text]})"

    def expand_lits(self, text: str) -> str:
        return re.sub(r'\(str\.lit "((?:[^"\\]|\\.)*)"\)', lambda m: self.lit(m.group(1)), text)

    # ---- module ----------------------------------------------------------------

    def emit(self) -> str:
        prelude = self.expand_lits(PRELUDE.read_text())
        funcs = [self.emit_func(f) for f in self.m.funcs]
        start = f'  (func $wpy.start (export "_start")\n    (call ${self.m.main})\n    (call $rt.flush))'
        class_types = self.class_types()
        vtables = self.vtable_globals()
        self.close_list_types()
        list_funcs = self.list_helpers()  # registers literals used by the helpers
        lits = self.literal_funcs()
        types = self.type_section(class_types)
        parts = [
            "(module",
            '  (import "wasi_snapshot_preview1" "fd_write" (func $wasi.fd_write (param i32 i32 i32 i32) (result i32)))',
            '  (import "wasi_snapshot_preview1" "proc_exit" (func $wasi.proc_exit (param i32)))',
            types,
            '  (memory (export "memory") 1)',
            f"  (data $lits {wat_string(bytes(self.lit_data))})",
            prelude,
            lits,
            list_funcs,
            vtables,
            *funcs,
            start,
            ")",
        ]
        return "\n".join(p for p in parts if p) + "\n"

    def close_list_types(self) -> None:
        """list[list[T]] needs list[T] too: register element list types transitively."""
        while True:
            n = len(self.list_types)
            for t in list(self.list_types.values()):
                self.array_elem(t.elem)
            if len(self.list_types) == n:
                return

    def type_section(self, class_types: list[str]) -> str:
        lines = ["  (rec",
                 "    (type $u8s (array (mut i8)))",
                 "    (type $str (struct (field $bytes (ref $u8s)) (field $hash (mut i32))))"]
        done: set[str] = set()
        while len(done) < len(self.list_types):  # element types can register further list types
            for m, t in list(self.list_types.items()):
                if m in done:
                    continue
                done.add(m)
                lines.append(f"    (type $arr.{m} (array (mut {self.array_elem(t.elem)})))")
                lines.append(f"    (type $list.{m} (struct (field $len (mut i32)) (field $data (mut (ref $arr.{m})))))")
        lines += class_types
        lines.append("  )")
        return "\n".join(lines)

    def class_types(self) -> list[str]:
        out: list[str] = []
        for cls in self.m.classes:
            sub = "final" if cls.is_final else ""
            sup_vt = f" ${cls.base.name}.vt" if cls.base else ""
            sup = f" ${cls.base.name}" if cls.base else ""
            for m in cls.methods.values():
                if m.decl is cls:
                    params = "".join(f" (param {self.valtype(t)})" for _, t in m.sig.params)
                    result = f" (result {self.valtype(m.sig.ret)})" if m.sig.ret != NONE else ""
                    out.append(f"    (type ${cls.name}.{m.name}.sig (func (param (ref ${cls.name})){params}{result}))")
            slots = "".join(f" (field ${m.name} (ref ${m.decl.name}.{m.name}.sig))" for m in cls.methods.values())
            out.append(f"    (type ${cls.name}.vt (sub {sub}{sup_vt} (struct{slots})))".replace("(sub  ", "(sub "))
            fields = "".join(
                f" (field ${f.name} {self.valtype(f.ty) if f.final else f'(mut {self.valtype(f.ty)})'})"
                for f in cls.fields.values())
            out.append(f"    (type ${cls.name} (sub {sub}{sup} (struct (field $__vt__ (ref ${cls.name}.vt)){fields})))"
                       .replace("(sub  ", "(sub "))
        return out

    def vtable_globals(self) -> str:
        out = []
        for cls in self.m.classes:
            refs = "".join(f" (ref.func ${m.owner.name}.{m.name})" for m in cls.methods.values())
            out.append(f"  (global ${cls.name}.vt.inst (ref ${cls.name}.vt) (struct.new ${cls.name}.vt{refs}))")
        return "\n".join(out)

    def literal_funcs(self) -> str:
        out = []
        for i, (off, n) in enumerate(self.lit_spans):
            out.append(
                f"  (global $lit.{i}.cache (mut (ref null $str)) (ref.null $str))\n"
                f"  (func $lit.{i} (result (ref $str))\n"
                f"    (if (ref.is_null (global.get $lit.{i}.cache))\n"
                f"      (then (global.set $lit.{i}.cache\n"
                f"        (struct.new $str (array.new_data $u8s $lits (i32.const {off}) (i32.const {n})) (i32.const 0)))))\n"
                f"    (ref.as_non_null (global.get $lit.{i}.cache)))")
        return "\n".join(out)

    # ---- list runtime, generated per element type ---------------------------------

    def list_helpers(self) -> str:
        out: list[str] = []
        done: set[str] = set()
        while len(done) < len(self.list_types):
            for m, t in list(self.list_types.items()):
                if m not in done:
                    done.add(m)
                    out.append(self.list_helper(m, t))
        return "\n".join(out)

    def list_helper(self, m: str, t: ListT) -> str:
        e = t.elem
        R = self.valtype(e)
        get = "array.get_u" if e == BOOL else "array.get"
        nn = e.is_ref and not isinstance(e, OptionalT)
        load = f"({get} $arr.{m} (struct.get $list.{m} $data (local.get $l)) (local.get $i))"
        if nn:
            load = f"(ref.as_non_null {load})"
        clear = (f"\n    (array.set $arr.{m} (local.get $d) (local.get $n) (ref.null {self.heap(e)}))"
                 "  ;; drop the reference so the GC can reclaim it" if e.is_ref else "")
        pop_val = "(ref.as_non_null (local.get $v))" if nn else "(local.get $v)"
        oob = self.lit("IndexError: list index out of range")
        oob_set = self.lit("IndexError: list assignment index out of range")
        empty = self.lit("IndexError: pop from empty list")
        code = f"""  (func $list.{m}.get (param $l (ref $list.{m})) (param $i i32) (result {R})
    (local $n i32)
    (local.set $n (struct.get $list.{m} $len (local.get $l)))
    (if (i32.lt_s (local.get $i) (i32.const 0)) (then (local.set $i (i32.add (local.get $i) (local.get $n)))))
    (if (i32.ge_u (local.get $i) (local.get $n)) (then (call $rt.panic {oob})))
    {load})
  (func $list.{m}.set (param $l (ref $list.{m})) (param $i i32) (param $v {R})
    (local $n i32)
    (local.set $n (struct.get $list.{m} $len (local.get $l)))
    (if (i32.lt_s (local.get $i) (i32.const 0)) (then (local.set $i (i32.add (local.get $i) (local.get $n)))))
    (if (i32.ge_u (local.get $i) (local.get $n)) (then (call $rt.panic {oob_set})))
    (array.set $arr.{m} (struct.get $list.{m} $data (local.get $l)) (local.get $i) (local.get $v)))
  (func $list.{m}.append (param $l (ref $list.{m})) (param $v {R})
    (local $n i32) (local $d (ref $arr.{m})) (local $grown (ref null $arr.{m}))
    (local.set $n (struct.get $list.{m} $len (local.get $l)))
    (local.set $d (struct.get $list.{m} $data (local.get $l)))
    (if (i32.eq (local.get $n) (array.len (local.get $d)))
      (then
        (local.set $grown (array.new_default $arr.{m}
          (select (i32.const 4) (i32.shl (local.get $n) (i32.const 1)) (i32.lt_u (local.get $n) (i32.const 2)))))
        (array.copy $arr.{m} $arr.{m} (local.get $grown) (i32.const 0) (local.get $d) (i32.const 0) (local.get $n))
        (local.set $d (ref.as_non_null (local.get $grown)))
        (struct.set $list.{m} $data (local.get $l) (local.get $d))))
    (array.set $arr.{m} (local.get $d) (local.get $n) (local.get $v))
    (struct.set $list.{m} $len (local.get $l) (i32.add (local.get $n) (i32.const 1))))
  (func $list.{m}.pop (param $l (ref $list.{m})) (result {R})
    (local $n i32) (local $d (ref $arr.{m})) (local $v {self.storage(e)})
    (local.set $n (struct.get $list.{m} $len (local.get $l)))
    (if (i32.eqz (local.get $n)) (then (call $rt.panic {empty})))
    (local.set $n (i32.sub (local.get $n) (i32.const 1)))
    (local.set $d (struct.get $list.{m} $data (local.get $l)))
    (local.set $v ({get} $arr.{m} (local.get $d) (local.get $n))){clear}
    (struct.set $list.{m} $len (local.get $l) (local.get $n))
    {pop_val})"""
        inner = e
        while isinstance(inner, ListT):
            inner = inner.elem
        if isinstance(inner, Prim):
            item = self.to_str_code(e, f"(call $list.{m}.get (local.get $l) (local.get $i))")
            code += f"""
  (func $list.{m}.to_str (param $l (ref $list.{m})) (result (ref $str))
    (local $s (ref null $str)) (local $i i32) (local $n i32)
    (local.set $s {self.lit("[")})
    (local.set $n (struct.get $list.{m} $len (local.get $l)))
    (block $done
      (loop $next
        (br_if $done (i32.ge_u (local.get $i) (local.get $n)))
        (if (local.get $i)
          (then (local.set $s (call $rt.str_concat (ref.as_non_null (local.get $s)) {self.lit(", ")}))))
        (local.set $s (call $rt.str_concat (ref.as_non_null (local.get $s)) {item}))
        (local.set $i (i32.add (local.get $i) (i32.const 1)))
        (br $next)))
    (call $rt.str_concat (ref.as_non_null (local.get $s)) {self.lit("]")}))"""
        return code

    def to_str_code(self, t: Type, code: str) -> str:
        if t == STR:
            return code
        if t == I32:
            return f"(call $rt.i64_to_str (i64.extend_i32_s {code}))"
        if t == I64:
            return f"(call $rt.i64_to_str {code})"
        if t == F32:
            return f"(call $rt.f64_to_str (f64.promote_f32 {code}))"
        if t == F64:
            return f"(call $rt.f64_to_str {code})"
        if t == BOOL:
            return f"(call $rt.bool_to_str {code})"
        if t == OptionalT(STR):
            tmp = self.fn.temp("(ref null $str)") if self.fn else None
            assert tmp, "Optional[str] to str needs a function context"
            return (f"(if (result (ref $str)) (ref.is_null (local.tee {tmp} {code})) "
                    f"(then {self.lit('None')}) (else (ref.as_non_null (local.get {tmp}))))")
        if isinstance(t, ListT):
            return f"(call $list.{mangle(t.elem)}.to_str {code})"
        raise AssertionError(f"to_str of {t}")

    # ---- functions -------------------------------------------------------------

    def emit_func(self, f: ir.Func) -> str:
        self.fn = FnState(f, self)
        body: list[str] = []
        if f.self_cast is not None:
            body.append(f"(local.set $self (ref.cast (ref ${f.self_cast.name}) (local.get $self.in)))")
        for s in f.body:
            body += self.stmt(s)
        if f.ret != NONE and not (f.body and isinstance(f.body[-1], ir.Return)):
            body.append("(unreachable)")
        head = f"  (func ${f.name}"
        if f.export:
            head += f' (export "{f.export}")'
        if f.sig_type:
            head += f" (type ${f.sig_type})"
        params = "".join(f" (param ${n} {self.valtype(t)})" for n, t in f.params)
        result = f" (result {self.valtype(f.ret)})" if f.ret != NONE else ""
        locals_ = [f"(local ${n} {self.valtype(t) if n in f.nonnull_locals else self.storage(t)})"
                   for n, t in f.locals.items()]
        locals_ += [f"(local {n} {t})" for n, t in self.fn.temps]
        lines = [head + params + result]
        if locals_:
            lines.append("    " + " ".join(locals_))
        lines += ["    " + line for line in body]
        lines[-1] += ")"
        self.fn = None
        return "\n".join(lines)

    # ---- statements ------------------------------------------------------------

    def block(self, stmts: list[ir.Stmt]) -> list[str]:
        out: list[str] = []
        for s in stmts:
            out += self.stmt(s)
        return out

    def stmt(self, s: ir.Stmt) -> list[str]:
        fn = self.fn
        assert fn is not None
        if isinstance(s, ir.SetLocal):
            return [f"(local.set ${s.name} {self.ex(s.value)})"]
        if isinstance(s, ir.SetField):
            return [f"(struct.set ${s.cls.name} ${s.field.name} {self.ex(s.obj)} {self.ex(s.value)})"]
        if isinstance(s, ir.SetIndex):
            m = mangle(s.lst.ty.elem)  # type: ignore[attr-defined]
            self.heap(s.lst.ty)
            return [f"(call $list.{m}.set {self.ex(s.lst)} {self.ex(s.index)} {self.ex(s.value)})"]
        if isinstance(s, ir.ExprStmt):
            code = self.ex(s.expr)
            return [code if s.expr.ty == NONE else f"(drop {code})"]
        if isinstance(s, ir.Return):
            return [f"(return {self.ex(s.value)})" if s.value is not None else "(return)"]
        if isinstance(s, ir.If):
            out = [f"(if {self.ex(s.cond)}", "  (then"]
            out += ["    " + line for line in self.block(s.then)]
            out[-1] += ")"
            if s.else_:
                out.append("  (else")
                out += ["    " + line for line in self.block(s.else_)]
                out[-1] += ")"
            out[-1] += ")"
            return out
        if isinstance(s, ir.Loop):
            n = fn.label()
            brk, cont, top = f"$brk.{n}", f"$cont.{n}", f"$top.{n}"
            fn.loops.append((brk, cont))
            out = [f"(block {brk}", f"  (loop {top}"]
            if s.cond is not None:
                out.append(f"    (br_if {brk} (i32.eqz {self.ex(s.cond)}))")
            out.append(f"    (block {cont}")
            out += ["      " + line for line in self.block(s.body)] or ["      (nop)"]
            out[-1] += ")"
            out += ["    " + line for line in self.block(s.step)]
            out.append(f"    (br {top})))")
            fn.loops.pop()
            return out
        if isinstance(s, ir.Break):
            return [f"(br {fn.loops[-1][0]})"]
        if isinstance(s, ir.Continue):
            return [f"(br {fn.loops[-1][1]})"]
        if isinstance(s, ir.Print):
            out = []
            for i, a in enumerate(s.args):
                if i:
                    out.append("(call $rt.out_byte (i32.const 32))")
                out.append(f"(call $rt.out_str {self.ex(a)})")
            out.append("(call $rt.out_byte (i32.const 10))")
            return out
        if isinstance(s, ir.Panic):
            return [f"(call $rt.panic {self.ex(s.msg)})"]
        raise AssertionError(f"unknown stmt {s}")

    # ---- expressions -----------------------------------------------------------

    def ex(self, e: ir.Expr) -> str:
        fn = self.fn
        assert fn is not None
        if isinstance(e, ir.Const):
            return self.const(e.ty, e.value)
        if isinstance(e, ir.StrLit):
            return self.lit(e.text)
        if isinstance(e, ir.NoneLit):
            return f"(ref.null {self.heap(e.ty)})"
        if isinstance(e, ir.LocalGet):
            code = f"(local.get ${e.name})"
            if e.ty.is_ref and not isinstance(e.ty, OptionalT):
                if e.ty != strip_optional(e.declared):
                    return f"(ref.cast (ref {self.heap(e.ty)}) {code})"  # narrowed by isinstance
                if e.name not in fn.nonnull:
                    return f"(ref.as_non_null {code})"
            return code
        if isinstance(e, ir.Unary):
            return self.unary(e)
        if isinstance(e, ir.Binary):
            return self.binary(e)
        if isinstance(e, ir.Compare):
            return self.compare(e)
        if isinstance(e, ir.IsNull):
            code = f"(ref.is_null {self.ex(e.operand)})"
            return f"(i32.eqz {code})" if e.negate else code
        if isinstance(e, ir.IsInstance):
            return f"(ref.test (ref ${e.cls.name}) {self.ex(e.operand)})"
        if isinstance(e, ir.Logical):
            l, r = self.ex(e.left), self.ex(e.right)
            if e.op == "and":
                return f"(if (result i32) {l} (then {r}) (else (i32.const 0)))"
            return f"(if (result i32) {l} (then (i32.const 1)) (else {r}))"
        if isinstance(e, ir.Convert):
            op = CONVERSIONS[(e.operand.ty, e.ty)]  # type: ignore[index]
            inner = self.ex(e.operand)
            return f"({op} {inner})" if op else inner
        if isinstance(e, ir.IfExpr):
            return (f"(if (result {self.valtype(e.ty)}) {self.ex(e.cond)} "
                    f"(then {self.ex(e.then)}) (else {self.ex(e.else_)}))")
        if isinstance(e, ir.Call):
            return self.call(f"${e.func}", e.args)
        if isinstance(e, ir.New):
            return self.call(f"${e.cls.name}.new", e.args)
        if isinstance(e, ir.CallMethod):
            return self.call_method(e)
        if isinstance(e, ir.StructNew):
            vals = "".join(" " + self.ex(v) for v in e.values)
            return f"(struct.new ${e.cls.name} (global.get ${e.cls.name}.vt.inst){vals})"
        if isinstance(e, ir.GetField):
            return f"(struct.get ${e.cls.name} ${e.field.name} {self.ex(e.obj)})"
        if isinstance(e, ir.ListLit):
            m = mangle(e.ty.elem)  # type: ignore[attr-defined]
            self.heap(e.ty)
            items = "".join(" " + self.ex(i) for i in e.items)
            return (f"(struct.new $list.{m} (i32.const {len(e.items)}) "
                    f"(array.new_fixed $arr.{m} {len(e.items)}{items}))")
        if isinstance(e, ir.ListGet):
            self.heap(e.lst.ty)
            return f"(call $list.{mangle(e.ty)}.get {self.ex(e.lst)} {self.ex(e.index)})"
        if isinstance(e, ir.ListLen):
            return f"(struct.get {self.heap(e.lst.ty)} $len {self.ex(e.lst)})"
        if isinstance(e, ir.ListPop):
            self.heap(e.lst.ty)
            return f"(call $list.{mangle(e.ty)}.pop {self.ex(e.lst)})"
        if isinstance(e, ir.ListAppend):
            m = mangle(e.lst.ty.elem)  # type: ignore[attr-defined]
            self.heap(e.lst.ty)
            return f"(call $list.{m}.append {self.ex(e.lst)} {self.ex(e.value)})"
        if isinstance(e, ir.StrLen):
            return f"(call $rt.str_len {self.ex(e.s)})"
        if isinstance(e, ir.StrConcat):
            return f"(call $rt.str_concat {self.ex(e.left)} {self.ex(e.right)})"
        if isinstance(e, ir.ToStr):
            if isinstance(e.operand.ty, ListT):
                self.heap(e.operand.ty)
            return self.to_str_code(e.operand.ty, self.ex(e.operand))
        raise AssertionError(f"unknown expr {e}")

    def const(self, t: Type, v: int | float | bool) -> str:
        if t == BOOL:
            return f"(i32.const {1 if v else 0})"
        if isinstance(t, Prim) and t.is_int:
            return f"({t.name}.const {int(v)})"
        fv = float(v)
        if fv != fv:
            lit = "nan"
        elif fv in (float("inf"), float("-inf")):
            lit = "inf" if fv > 0 else "-inf"
        else:
            lit = repr(fv)
        return f"({t.name}.const {lit})"  # type: ignore[attr-defined]

    def call(self, target: str, args: list[ir.Expr]) -> str:
        return f"(call {target}" + "".join(" " + self.ex(a) for a in args) + ")"

    def call_method(self, e: ir.CallMethod) -> str:
        cls, m = e.cls, e.method
        # Closed world: if every class reachable from the static type shares one
        # implementation, call it directly.
        impls = {c.methods[m.name].owner for c in [cls, *cls.all_subclasses()]}
        if len(impls) == 1:
            return self.call(f"${impls.pop().name}.{m.name}", [e.recv, *e.args])
        fn = self.fn
        assert fn is not None
        args = "".join(" " + self.ex(a) for a in e.args)
        r = e.recv
        if isinstance(r, ir.LocalGet) and r.ty == strip_optional(r.declared):
            recv, again = self.ex(r), f"(local.get ${r.name})"  # re-read the local for the vtable
        else:
            tmp = fn.temp(f"(ref null ${cls.name})")
            recv, again = f"(ref.as_non_null (local.tee {tmp} {self.ex(r)}))", f"(local.get {tmp})"
        vt = f"(struct.get ${cls.name} $__vt__ {again})"
        return f"(call_ref ${m.decl.name}.{m.name}.sig {recv}{args} (struct.get ${cls.name}.vt ${m.name} {vt}))"

    def unary(self, e: ir.Unary) -> str:
        t = e.ty
        v = self.ex(e.operand)
        if e.op == "not":
            return f"(i32.eqz {v})"
        assert isinstance(t, Prim)
        if e.op == "neg":
            return f"({t.name}.neg {v})" if t.is_float else f"({t.name}.sub ({t.name}.const 0) {v})"
        if e.op == "invert":
            return f"({t.name}.xor {v} ({t.name}.const -1))"
        if e.op == "abs":
            if t.is_float:
                return f"({t.name}.abs {v})"
            tmp = self.fn.temp(t.name)  # type: ignore[union-attr]
            # select evaluates its operands in order, so the tee must come first
            return (f"(select ({t.name}.sub ({t.name}.const 0) (local.tee {tmp} {v})) (local.get {tmp}) "
                    f"({t.name}.lt_s (local.get {tmp}) ({t.name}.const 0)))")
        raise AssertionError(e.op)

    def binary(self, e: ir.Binary) -> str:
        t = e.ty
        assert isinstance(t, Prim)
        l, r = self.ex(e.left), self.ex(e.right)
        n = t.name
        if e.op in SIMPLE_BIN:
            return f"({n}.{SIMPLE_BIN[e.op]} {l} {r})"
        if t.is_int:
            if e.op in ("floordiv", "mod"):
                return f"(call $rt.{n}.{e.op} {l} {r})"
            if e.op in ("min", "max"):
                a, b = self.fn.temp(n), self.fn.temp(n)  # type: ignore[union-attr]
                cmp = "lt_s" if e.op == "min" else "gt_s"
                return (f"(select (local.tee {a} {l}) (local.tee {b} {r}) "
                        f"({n}.{cmp} (local.get {a}) (local.get {b})))")
        else:
            if e.op in ("min", "max"):
                return f"({n}.{e.op} {l} {r})"
            if e.op in ("div", "floordiv", "mod"):
                if t == F64:
                    return f"(call $rt.f64.{e.op} {l} {r})"
                return f"(f32.demote_f64 (call $rt.f64.{e.op} (f64.promote_f32 {l}) (f64.promote_f32 {r})))"
        raise AssertionError(f"binary {e.op} on {t}")

    def compare(self, e: ir.Compare) -> str:
        l, r = self.ex(e.left), self.ex(e.right)
        t = e.operand_ty
        if e.op in ("is", "isnot"):
            code = f"(ref.eq {l} {r})"
            return f"(i32.eqz {code})" if e.op == "isnot" else code
        if t == STR:
            if e.op == "eq":
                return f"(call $rt.str_eq {l} {r})"
            if e.op == "ne":
                return f"(i32.eqz (call $rt.str_eq {l} {r}))"
            return f"(i32.{INT_CMP[e.op]} (call $rt.str_cmp {l} {r}) (i32.const 0))"
        if t == BOOL:
            return f"(i32.{e.op} {l} {r})"
        assert isinstance(t, Prim)
        table = FLOAT_CMP if t.is_float else INT_CMP
        return f"({t.name}.{table[e.op]} {l} {r})"


def emit_module(module: ir.Module) -> str:
    return Emitter(module).emit()
