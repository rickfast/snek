"""Typed IR -> WebAssembly text (WAT) using WasmGC and Wasm exception handling.

Layout decisions (see DESIGN.md):
  str          (struct $bytes $hash $cp $idx): UTF-8 bytes, cached hash, cached
               code point count (ASCII iff it equals the byte length) and a lazily
               built code point -> byte offset index
  list[T]      (struct (field $len (mut i32)) (field $data (mut (ref $arr.T))))
               monomorphized per T; list[bool] uses a packed (array (mut i8))
  class C      (struct (field $__vt__ (ref $C.vt)) (field $f T) ...)
               single inheritance = WasmGC struct subtyping; vtables are structs
               holding the class name and typed funcrefs, one immutable global
               instance per class
  T | None     (ref null $T); everything else is non-nullable
  exceptions   one tag, (tag $snek.exc (param (ref $BaseException))); `except C`
               is a ref.test on the caught object, so handlers follow the class
               hierarchy exactly like isinstance
  locals       reference locals are stored nullable and read with ref.as_non_null
               (or ref.cast when narrowed); wasm-opt removes most of these

The whole program is known at compile time, so classes without subclasses are
emitted `final` and calls are devirtualized when only one implementation of a
method is reachable from the receiver's static type.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from pathlib import Path

from . import ir, unicode
from .collections_wat import CollectionEmitter
from .closures_wat import ClosureEmitter
from .list_builtins import list_builtin_code
from .keyed_builtins import keyed_builtin_code
from .protocols_wat import ProtocolEmitter
from .machines import MachineEmitter, has_await
from .types import (
    BOOL,
    BUF,
    F32,
    F64,
    I32,
    I64,
    NONE,
    STR,
    HOST_OBJECT,
    StreamT,
    TRACEBACK,
    TypeT,
    ClassInfo,
    ClassT,
    DictT,
    DictViewT,
    ListT,
    IteratorT,
    CoroutineT,
    CallableT,
    CellT,
    EnvT,
    TupleT,
    OptionalT,
    Prim,
    Type,
    has_repr,
    strip_optional,
)

PRELUDE = Path(__file__).with_name("prelude.wat")
EXC = "$BaseException"  # payload type of the exception tag

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

# Pending-action codes for leaving a try/finally region (see Emitter.try_finally).
EXCEPTION, FALLTHROUGH = -1, 0
JUMP_CODES = {"return": 1, "break": 2, "continue": 3, "exit_inline": 4}

# float -> int conversions are checked (nan/inf/out of range raise, like CPython's int())
CHECKED_TRUNC = {(F64, I32): "$rt.f64_to_i32", (F64, I64): "$rt.f64_to_i64",
                 (F32, I32): "$rt.f64_to_i32", (F32, I64): "$rt.f64_to_i64"}


def words(values: list[int], size: int = 4) -> bytes:
    """Little-endian data for a table of (possibly negative) integers."""
    mask = (1 << (8 * size)) - 1
    return b"".join((v & mask).to_bytes(size, "little") for v in values)


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
    """Name fragment for a monomorphized type: list[list[Node | None]] -> list<list<Node?>>.
    `<`, `>` and `?` are legal in WAT names but not in Python identifiers, so these never
    collide with user class names."""
    if t == NONE:
        return "none"
    if isinstance(t, CallableT):
        return "callable<" + "~".join(mangle(p) for p in t.params) + ":" + mangle(t.ret) + ">"
    if isinstance(t, CellT):
        return f"cell<{mangle(t.elem)}>"
    if isinstance(t, EnvT):
        return "env<>" if t.captures is None else "env<" + "~".join(mangle(p) for p in t.captures) + ">.scope"
    if isinstance(t, (IteratorT, CoroutineT)):
        return f"{'iter' if isinstance(t, IteratorT) else 'coro'}<{mangle(t.elem)}>"
    if isinstance(t, Prim):
        return t.name
    if t == STR:
        return "str"
    if t == HOST_OBJECT:
        return "HostObject"
    if isinstance(t, StreamT):
        return "stream." + t.kind
    if isinstance(t, ListT):
        return f"list<{mangle(t.elem)}>"
    if isinstance(t, TupleT):
        return "tuple<" + "~".join(mangle(e) for e in t.elems) + ">"
    if isinstance(t, DictT):
        return f"dict<{mangle(t.key)}~{mangle(t.value)}>"
    if isinstance(t, DictViewT):
        return mangle(t.mapping) + "." + t.kind + ".view"
    if isinstance(t, ClassT):
        return t.info.name
    if isinstance(t, TypeT):
        return f"type<{t.info.name}>"
    if t == TRACEBACK:
        return "traceback<>"
    if isinstance(t, OptionalT):
        return mangle(t.inner) + "?"
    raise AssertionError(t)


def indent(lines: list[str], n: int = 2) -> list[str]:
    pad = " " * n
    return [pad + line for line in lines]


def closed(lines: list[str]) -> list[str]:
    """Append ')' to the last line, inserting a nop if there is nothing to close over."""
    lines = lines or ["(nop)"]
    lines[-1] += ")"
    return lines


@dataclass
class LoopCtl:
    brk: str
    cont: str


@dataclass
class InlineCtl:
    exit: str


@dataclass
class FinallyCtl:
    exit: str  # label of the block that leads to the finally body
    k: str  # local holding the pending action
    ret: str | None = None  # local holding a pending return value
    used: set[str] = field(default_factory=set)


class FnState:
    def __init__(self, f: ir.Func):
        self.f = f
        self.ctl: list[LoopCtl | FinallyCtl | InlineCtl] = []
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


class Emitter(CollectionEmitter, ProtocolEmitter, MachineEmitter, ClosureEmitter):
    def __init__(self, module: ir.Module, component: bool = False, *, graph_factory=None, machine_backend=None):
        self.host_objects = False
        self.m = module
        self.graph_factory = graph_factory
        self.machine_backend = machine_backend
        self.component = component
        self.lit_index: dict[str, int] = {}
        self.lit_data = bytearray()
        self.lit_spans: list[tuple[int, int, int]] = []  # byte offset, byte length, code points
        self.list_types: dict[str, ListT] = {}
        self.list_ops: dict[str, set[str]] = {}  # extra list helpers each element type needs
        self.tuple_types: dict[str, TupleT] = {}
        self.dict_types: dict[str, DictT] = {}
        self.view_types: dict[str, DictViewT] = {}
        self.collection_ops: dict[str, set[str]] = {}
        self.optional_reprs: dict[str, Type] = {}
        self.object_equalities: dict[str, tuple[Type, Type]] = {}
        self.class_strings: set[ClassInfo] = set()
        self.string_builders: set[str] = set()
        self.fn: FnState | None = None
        self.machine_types = {}
        self.machine_graphs = []
        self.wait_groups: dict[str, list[str]] = {}
        self.wait_results: dict[str, list[str]] = {}
        self.main_async = True
        self.closure_types = {}
        self.cell_types = {}
        self.env_types = {}
        self.function_refs = {}
        self.closure_refs = set()
        self.keyed_builtins = {}
        self.byte_io = False
        self.stdlib_io: set[str] = set()
        self.wasi_features: set[str] = set()

    # ---- types -----------------------------------------------------------------

    def heap(self, t: Type) -> str:
        t = strip_optional(t)
        if t == HOST_OBJECT:
            self.host_objects = True
            return "extern"
        if isinstance(t, (CallableT, CellT, EnvT)):
            name = mangle(t)
            registry = (self.closure_types if isinstance(t, CallableT) else
                        self.cell_types if isinstance(t, CellT) else self.env_types)
            if not (isinstance(t, EnvT) and t.captures is None) and name not in registry:
                registry[name] = t
                children = (*t.params, t.ret) if isinstance(t, CallableT) else ((t.elem,) if isinstance(t, CellT) else t.captures)
                for p in children:
                    if p != NONE:
                        self.storage(p)
            return "$" + name
        if t == STR:
            return "$str"
        if isinstance(t, StreamT):
            return "$io.stream"
        if t == BUF:
            return "$buf"
        if isinstance(t, (IteratorT, CoroutineT)):
            self.machine_types.setdefault(mangle(t), t)
            if t.elem != NONE:
                self.storage(t.elem)
            return "$" + mangle(t)
        if isinstance(t, ListT):
            m = mangle(t.elem)
            self.list_types.setdefault(m, t)
            return f"$list.{m}"
        if isinstance(t, (TupleT, DictT, DictViewT)):
            m = mangle(t)
            registry = (self.tuple_types if isinstance(t, TupleT) else
                        self.dict_types if isinstance(t, DictT) else self.view_types)
            registry.setdefault(m, t)
            return f"${m}"
        if isinstance(t, ClassT):
            return f"${t.info.name}"
        if isinstance(t, TypeT):
            return f"${t.info.name}.vt"
        if t == TRACEBACK:
            return "$traceback<>"
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
            self.lit_spans.append((len(self.lit_data), len(data), len(text)))
            self.lit_data += data
        return f"(call $lit.{self.lit_index[text]})"

    def expand_lits(self, text: str) -> str:
        return re.sub(r'\(str\.lit "((?:[^"\\]|\\.)*)"\)', lambda m: self.lit(m.group(1)), text)

    # ---- module ----------------------------------------------------------------

    def emit(self) -> str:
        from . import component
        prelude = self.expand_lits(PRELUDE.read_text())
        funcs = []
        for f in self.m.funcs:
            if f.host:
                for _, ty in f.params: self.valtype(ty)
                if f.ret != NONE: self.valtype(f.ret)
                continue  # emitted by the shared library ABI generator
            if f.name == self.m.main and not has_await(f.body):
                # Preserve the direct structured path for synchronous programs.
                # Components still need an async command shell, but not a CFG
                # and spilled locals for an entirely synchronous main body.
                name = f.name + ".body" if self.component else f.name
                funcs.append(self.emit_func(replace(f, name=name, ret=NONE, machine=False)))
                if not self.component:
                    self.main_async = False
                    continue
                f = ir.Func(f.name, [], CoroutineT(NONE), {}, [ir.ExprStmt(ir.Call(NONE, name, []))], machine=True)
            funcs.append(self.emit_func(f))
        funcs.append(self.emit_machine(ir.Func("snek.checkpoint", [], CoroutineT(NONE), {},
                                              [ir.Yield(None)], machine=True)))
        from . import stdlib_io
        funcs.extend(stdlib_io.machines(self))
        from . import stdlib_host
        funcs.extend(stdlib_host.machines(self))
        from bootstrap.tasks import wait_all_code
        for name, heaps in self.wait_groups.items():
            operation = "gather" if name in self.wait_results else "wait_all"
            funcs.append(wait_all_code(name, heaps, self.lit(operation + "() requires distinct, unawaited coroutines"), *self.wait_results.get(name, [])))
        class_types = self.class_types()
        vtables = self.vtable_globals()
        closures = self.closure_artifacts()
        keyed = "\n".join(keyed_builtin_code(self, name, *spec) for name, spec in self.keyed_builtins.items())
        from .byte_io import helpers as byte_io_helpers
        byte_io = byte_io_helpers(self) if self.byte_io else ""
        library_io = stdlib_io.helpers(self) if self.stdlib_io else '(func $snek.sys.reset)'
        library_host = stdlib_host.helpers(self)
        component_runtime = ""
        if self.component:
            component_runtime = component.RUNTIME.read_text()
            component_runtime += (self.expand_lits(component.INPUT_RUNTIME.read_text()) if "stream_read" in self.stdlib_io else
                                  '(func $io.input-pump (param i32 i32 i32) (result i32) (i32.const 0))\n(func $io.input-close)')
        self.close_collection_types()
        list_funcs = self.collection_helpers()  # registers all nested helpers and literals
        protocols = self.object_equality_helpers() + "\n" + self.class_string_helpers()
        machines = self.machine_helpers()
        lits = self.literal_funcs()
        tables = self.unicode_tables()  # after every literal is known
        types = self.type_section(class_types)
        imports = (component.core_imports(stdin="stream_read" in self.stdlib_io, features=self.wasi_features) if self.component else
                   '  (import "wasi_snapshot_preview1" "fd_write" (func $wasi.fd_write (param i32 i32 i32 i32) (result i32)))\n'
                   '  (import "wasi_snapshot_preview1" "proc_exit" (func $wasi.proc_exit (param i32)))')
        if self.byte_io or (not self.component and "stream_read" in self.stdlib_io):
            imports += '\n  (import "wasi_snapshot_preview1" "fd_read" (func $wasi.fd_read (param i32 i32 i32 i32) (result i32)))'
        if not self.component:
            imports += '\n' + stdlib_host.core_imports(self.wasi_features)
        parts = [
            "(module",
            imports,
            types,
            f"  (tag $snek.exc (param (ref {EXC})))",
            '' if self.component else '  (memory (export "memory") 1)',
            tables,
            prelude,
            byte_io,
            library_io,
            library_host,
            lits,
            list_funcs,
            keyed,
            protocols,
            machines,
            self.string_builder_helpers(),
            vtables,
            closures,
            *funcs,
            component_runtime if self.component else self.start_func(),
            ")",
        ]
        return "\n".join(p for p in parts if p) + "\n"

    def start_func(self) -> str:
        # An exception escaping __main__ is reported like CPython's last traceback line, exit 1.
        run = ([f"          (local.set $main (call ${self.m.main}))",
                "          (loop $run (br_if $run (call $coro<none>.advance (local.get $main)))))"]
               if self.main_async else [f"          (call ${self.m.main}))"])
        return "\n".join([
            '  (func $snek.start (export "_start")',
            '    (local $main (ref $coro<none>))',
            '    (call $lib.reset)',
            '    (call $snek.sys.reset)',
            "    (call $rt.uncaught",
            f"      (block $uncaught (result (ref {EXC}))",
            "        (try_table (catch $snek.exc $uncaught)",
            *run,
            "        (call $rt.flush)",
            "        (return))))",
        ])

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
                 "    (type $traceback<> (struct))",
                 "    (type $u8s (array (mut i8)))",
                 "    (type $u16s (array (mut i16)))",
                 "    (type $u32s (array (mut i32)))",
                 "    (type $strs (array (ref $str)))",
                 "    (type $ostrs (array (mut (ref null $str))))",
                 "    (type $str (struct (field $bytes (ref $u8s)) (field $hash (mut i32)) (field $cp (mut i32))"
                 " (field $idx (mut (ref null $u32s)))))",
                 "    (type $buf (struct (field $b (mut (ref $u8s))) (field $n (mut i32)) (field $cp (mut i32))))"]
        lines += ["    (type $io.state (struct (field $fd i32) (field $closed (mut i32))))",
                  "    (type $io.stream (struct (field $state (ref $io.state)) (field $buffer (ref null $io.stream))))"]
        done: set[str] = set()
        while len(done) < len(self.list_types):  # element types can register further list types
            for m, t in list(self.list_types.items()):
                if m in done:
                    continue
                done.add(m)
                lines.append(f"    (type $arr.{m} (array (mut {self.array_elem(t.elem)})))")
                lines.append(f"    (type $list.{m} (struct (field $len (mut i32)) (field $data (mut (ref $arr.{m})))))")
        lines += class_types
        lines += self.collection_types()
        lines += self.machine_type_section()
        lines += self.closure_types_wat()
        if self.component:
            lines.append('    (type $io.chunk (struct (field $fd i32) (field $bytes (ref $u8s)) (field $next (mut (ref null $io.chunk)))))')
        lines.append("  )")
        return "\n".join(lines)

    def class_types(self) -> list[str]:
        out: list[str] = []
        for cls in self.m.classes:
            sub = "sub final" if cls.is_final else "sub"
            sup_vt = f" ${cls.base.name}.vt" if cls.base else ""
            sup = f" ${cls.base.name}" if cls.base else ""
            for m in cls.methods.values():
                if m.decl is cls:
                    params = "".join(f" (param {self.valtype(t)})" for _, t in m.sig.params)
                    result = f" (result {self.valtype(m.sig.ret)})" if m.sig.ret != NONE else ""
                    out.append(f"    (type ${cls.name}.{m.name}.sig (func (param (ref ${cls.name})){params}{result}))")
            slots = "".join(f" (field ${m.name} (ref ${m.decl.name}.{m.name}.sig))" for m in cls.methods.values())
            out.append(f"    (type ${cls.name}.vt ({sub}{sup_vt} (struct (field $__name__ (ref $str)){slots})))")
            fields = "".join(
                f" (field ${f.name} {self.valtype(f.ty) if f.final else f'(mut {self.valtype(f.ty)})'})"
                for f in cls.fields.values())
            out.append(f"    (type ${cls.name} ({sub}{sup} (struct (field $__vt__ (ref ${cls.name}.vt)){fields})))")
        return out

    def vtable_globals(self) -> str:
        out = []
        for cls in self.m.classes:
            # Constant-expression string for the class name (array.new_fixed is a const op).
            data = (cls.name.rsplit(":", 1)[-1] if cls.name.startswith("@user:") else cls.name).encode()
            chars = " ".join(f"(i32.const {b})" for b in data)
            out.append(f"  (global ${cls.name}.name (ref $str) (struct.new $str "
                       f"(array.new_fixed $u8s {len(data)} {chars}) (i32.const 0) (i32.const {len(data)}) "
                       f"(ref.null $u32s)))")
            refs = "".join(f" (ref.func ${m.owner.name}.{m.name})" for m in cls.methods.values())
            out.append(f"  (global ${cls.name}.vt.inst (ref ${cls.name}.vt) "
                       f"(struct.new ${cls.name}.vt (global.get ${cls.name}.name){refs}))")
        return "\n".join(out)

    def unicode_tables(self) -> str:
        """Unicode properties of the code points the program can hold (see unicode.py)."""
        t = unicode.build(list(self.lit_index), self.m.unicode)
        cases = [len(t.case_runs[k]) for k in unicode.CASE_KINDS]
        cases += [x for k in unicode.CASE_KINDS for run in t.case_runs[k] for x in run]
        flags = [x for run in t.flag_runs for x in run]
        return "\n".join([
            f"  (data $uascii {wat_string(words(t.ascii_flags, 2))})",
            f"  (data $uflags {wat_string(words(flags))})",
            f"  (global $rt.uf.n i32 (i32.const {len(t.flag_runs)}))",
            f"  (data $ucases {wat_string(words(cases))})",
            f"  (global $rt.uc.n i32 (i32.const {len(cases)}))",
            f"  (data $uspecial {wat_string(words(t.specials))})",
            f"  (global $rt.usp.n i32 (i32.const {len(t.specials)}))",
            f"  (data $uzeros {wat_string(words(t.zeros))})",
            f"  (global $rt.uz.n i32 (i32.const {len(t.zeros)}))",
        ])

    def literal_funcs(self) -> str:
        out = []
        # One passive segment per literal, so wasm-opt drops the bytes of unused ones with their functions.
        for i, (off, n, cps) in enumerate(self.lit_spans):
            out.append(
                f"  (data $lit.{i} {wat_string(bytes(self.lit_data[off:off + n]))})\n"
                f"  (global $lit.{i}.cache (mut (ref null $str)) (ref.null $str))\n"
                f"  (func $lit.{i} (result (ref $str))\n"
                f"    (if (ref.is_null (global.get $lit.{i}.cache))\n"
                f"      (then (global.set $lit.{i}.cache\n"
                f"        (call $rt.mkstr (array.new_data $u8s $lit.{i} (i32.const 0) (i32.const {n})) (i32.const {cps})))))\n"
                f"    (ref.as_non_null (global.get $lit.{i}.cache)))")
        return "\n".join(out)

    # ---- list runtime, generated per element type ---------------------------------

    def list_op(self, t: ListT, op: str) -> str:
        """Name of the helper for a list operation, generating it on demand."""
        self.heap(t)
        m = mangle(t.elem)
        self.list_ops.setdefault(m, set()).add(op)
        return f"$list.{m}.{op}"

    def eq_code(self, t: Type, a: str, b: str) -> str:
        """==, as `in`, index(), count(), remove() and list equality use it."""
        if t in (BOOL, I32):
            return f"(i32.eq {a} {b})"
        if isinstance(t, Prim):
            return f"({t.name}.eq {a} {b})"
        if t == STR:
            return f"(call $rt.str_eq {a} {b})"
        if t == OptionalT(STR):
            return f"(call $rt.opt_str_eq {a} {b})"
        inner = strip_optional(t)
        if inner == HOST_OBJECT:
            return f"(call $__snek_object_same {a} {b})"
        if isinstance(inner, ClassT):
            return self.object_eq(t, t, a, b, identity=True)
        if isinstance(inner, ListT):
            return f"(call {self.list_op(inner, 'eq')} {a} {b})"
        if isinstance(inner, (TupleT, DictT)):
            return f"(call {self.collection_op(inner, 'eq')} {a} {b})"
        return f"(ref.eq {a} {b})"

    def list_op_code(self, m: str, t: ListT, op: str) -> str:
        if op == "sorted" or op.startswith(("sum_", "min", "max")):
            return list_builtin_code(self, t, op)
        e = t.elem
        A, L = f"$arr.{m}", f"$list.{m}"
        R, S = self.valtype(e), self.storage(e)
        get = "array.get_u" if e == BOOL else "array.get"
        nn = e.is_ref and not isinstance(e, OptionalT)
        LEN, DATA = f"(struct.get {L} $len (local.get $l))", f"(struct.get {L} $data (local.get $l))"

        def load(arr: str, idx: str) -> str:
            code = f"({get} {A} (local.get {arr}) (local.get {idx}))"
            return f"(ref.as_non_null {code})" if nn else code

        clear = (f"(array.set {A} (local.get $d) (local.get $n) (ref.null {self.heap(e)}))" if e.is_ref else "")
        shift_down = (f"(array.copy {A} {A} (local.get $d) (local.get $i) (local.get $d) "
                      f"(i32.add (local.get $i) (i32.const 1)) (i32.sub (local.get $n) (local.get $i)))")
        loop = lambda var, end, body: (  # noqa: E731
            f"(block $done (loop $next (br_if $done (i32.ge_s (local.get {var}) {end})) {body} "
            f"(local.set {var} (i32.add (local.get {var}) (i32.const 1))) (br $next)))")
        clamp = lambda v: (  # noqa: E731
            f"(if (i32.lt_s (local.get {v}) (i32.const 0)) (then (local.set {v} (i32.add (local.get {v}) "
            f"(local.get $n))) (if (i32.lt_s (local.get {v}) (i32.const 0)) (then (local.set {v} (i32.const 0))))))")
        head = f"  (func {L}.{op} "
        if op == "slice":
            body = f"""(param $l (ref {L})) (param $start i32) (param $stop i32) (param $step i32) (param $flags i32) (result (ref {L}))
    (local $cnt i32) (local $o (ref {A})) (local $d (ref {A})) (local $k i32) (local $p i32)
    (call $rt.step_check (local.get $step))
    (local.set $cnt (call $rt.slice_adj {LEN} (local.get $start) (local.get $stop) (local.get $step) (local.get $flags)))
    (local.set $p (global.get $rt.slice_start))
    (local.set $d {DATA})
    (local.set $o (array.new_default {A} (local.get $cnt)))
    (if (i32.eq (local.get $step) (i32.const 1))
      (then (array.copy {A} {A} (local.get $o) (i32.const 0) (local.get $d) (local.get $p) (local.get $cnt)))
      (else {loop("$k", "(local.get $cnt)",
                  f"(array.set {A} (local.get $o) (local.get $k) ({get} {A} (local.get $d) (local.get $p))) "
                  f"(local.set $p (i32.add (local.get $p) (local.get $step)))")}))
    (struct.new {L} (local.get $cnt) (local.get $o)))"""
        elif op == "copy":
            body = f"""(param $l (ref {L})) (result (ref {L}))
    (call {L}.slice (local.get $l) (i32.const 0) (i32.const 0) (i32.const 1) (i32.const 0)))"""
            self.list_ops[m].add("slice")
        elif op == "find":  # first index in [i, stop) equal to v, or -1
            body = f"""(param $l (ref {L})) (param $v {R}) (param $i i32) (param $stop i32) (result i32)
    (local $d (ref {A}))
    {loop("$i", "(local.get $stop)", f"(br_if $done (i32.ge_u (local.get $i) {LEN})) (local.set $d {DATA}) "
          f"(if {self.eq_code(e, load('$d', '$i'), '(local.get $v)')} (then (return (local.get $i))))")}
    (i32.const -1))"""
        elif op == "contains":
            self.list_ops[m].add("find")
            body = f"""(param $l (ref {L})) (param $v {R}) (result i32)
    (i32.ge_s (call {L}.find (local.get $l) (local.get $v) (i32.const 0) (i32.const 0x7FFFFFFF)) (i32.const 0)))"""
        elif op == "index":
            self.list_ops[m].add("find")
            msg = f"(call $rt.str_concat {self.repr_code(e, '(local.get $v)')} {self.lit(' is not in list')})"
            body = f"""(param $l (ref {L})) (param $v {R}) (param $start i32) (param $stop i32) (result i32)
    (local $n i32) (local $r i32)
    (local.set $n {LEN})
    {clamp("$start")}
    {clamp("$stop")}
    (local.set $r (call {L}.find (local.get $l) (local.get $v) (local.get $start) (local.get $stop)))
    (if (i32.lt_s (local.get $r) (i32.const 0)) (then (throw $snek.exc (call $ValueError.__new__ {msg}))))
    (local.get $r))"""
        elif op == "count":
            body = f"""(param $l (ref {L})) (param $v {R}) (result i32)
    (local $d (ref {A})) (local $i i32) (local $c i32)
    {loop("$i", LEN, f"(local.set $d {DATA}) (if {self.eq_code(e, load('$d', '$i'), '(local.get $v)')} (then (local.set $c (i32.add (local.get $c) (i32.const 1)))))")}
    (local.get $c))"""
        elif op == "insert":
            body = f"""(param $l (ref {L})) (param $i i32) (param $v {R})
    (local $n i32) (local $d (ref {A}))
    (local.set $n {LEN})
    {clamp("$i")}
    (if (i32.gt_s (local.get $i) (local.get $n)) (then (local.set $i (local.get $n))))
    (call {L}.append (local.get $l) (local.get $v))
    (local.set $d {DATA})
    (array.copy {A} {A} (local.get $d) (i32.add (local.get $i) (i32.const 1)) (local.get $d) (local.get $i)
                (i32.sub (local.get $n) (local.get $i)))
    (array.set {A} (local.get $d) (local.get $i) (local.get $v)))"""
        elif op == "extend":
            body = f"""(param $l (ref {L})) (param $o (ref {L}))
    (local $n i32) (local $k i32) (local $need i32) (local $cap i32) (local $d (ref {A})) (local $g (ref null {A}))
    (local.set $n {LEN})
    (local.set $k (struct.get {L} $len (local.get $o)))  ;; before growing: o may be l itself
    (local.set $need (i32.add (local.get $n) (local.get $k)))
    (local.set $d {DATA})
    (if (i32.gt_u (local.get $need) (array.len (local.get $d)))
      (then
        (local.set $cap (i32.shl (array.len (local.get $d)) (i32.const 1)))
        (if (i32.lt_u (local.get $cap) (local.get $need)) (then (local.set $cap (local.get $need))))
        (local.set $g (array.new_default {A} (local.get $cap)))
        (array.copy {A} {A} (local.get $g) (i32.const 0) (local.get $d) (i32.const 0) (local.get $n))
        (local.set $d (ref.as_non_null (local.get $g)))
        (struct.set {L} $data (local.get $l) (local.get $d))))
    (array.copy {A} {A} (local.get $d) (local.get $n) (struct.get {L} $data (local.get $o)) (i32.const 0) (local.get $k))
    (struct.set {L} $len (local.get $l) (local.get $need)))"""
        elif op == "remove":
            self.list_ops[m].add("find")
            body = f"""(param $l (ref {L})) (param $v {R})
    (local $i i32) (local $n i32) (local $d (ref {A}))
    (local.set $i (call {L}.find (local.get $l) (local.get $v) (i32.const 0) (i32.const 0x7FFFFFFF)))
    (if (i32.lt_s (local.get $i) (i32.const 0))
      (then {self.raise_code("ValueError", "list.remove(x): x not in list")}))
    (if (i32.ge_u (local.get $i) {LEN}) (then (return)))  ;; __eq__ may have shrunk the list
    (local.set $n (i32.sub {LEN} (i32.const 1)))
    (local.set $d {DATA})
    {shift_down}
    {clear}
    (struct.set {L} $len (local.get $l) (local.get $n)))"""
        elif op == "pop_at":
            val = "(ref.as_non_null (local.get $v))" if nn else "(local.get $v)"
            body = f"""(param $l (ref {L})) (param $i i32) (result {R})
    (local $n i32) (local $d (ref {A})) (local $v {S})
    (local.set $n {LEN})
    (if (i32.eqz (local.get $n)) (then {self.raise_code("IndexError", "pop from empty list")}))
    (if (i32.lt_s (local.get $i) (i32.const 0)) (then (local.set $i (i32.add (local.get $i) (local.get $n)))))
    (if (i32.ge_u (local.get $i) (local.get $n)) (then {self.raise_code("IndexError", "pop index out of range")}))
    (local.set $d {DATA})
    (local.set $v ({get} {A} (local.get $d) (local.get $i)))
    (local.set $n (i32.sub (local.get $n) (i32.const 1)))
    {shift_down}
    {clear}
    (struct.set {L} $len (local.get $l) (local.get $n))
    {val})"""
        elif op == "reverse":
            body = f"""(param $l (ref {L}))
    (local $d (ref {A})) (local $i i32) (local $j i32) (local $t {S})
    (local.set $d {DATA})
    (local.set $j (i32.sub {LEN} (i32.const 1)))
    (block $done
      (loop $next
        (br_if $done (i32.ge_s (local.get $i) (local.get $j)))
        (local.set $t ({get} {A} (local.get $d) (local.get $i)))
        (array.set {A} (local.get $d) (local.get $i) ({get} {A} (local.get $d) (local.get $j)))
        (array.set {A} (local.get $d) (local.get $j) (local.get $t))
        (local.set $i (i32.add (local.get $i) (i32.const 1)))
        (local.set $j (i32.sub (local.get $j) (i32.const 1)))
        (br $next))))"""
        elif op == "clear":
            body = f"""(param $l (ref {L}))
    (struct.set {L} $len (local.get $l) (i32.const 0))
    (struct.set {L} $data (local.get $l) (array.new_default {A} (i32.const 0))))"""
        elif op == "concat":
            body = f"""(param $a (ref {L})) (param $b (ref {L})) (result (ref {L}))
    (local $na i32) (local $nb i32) (local $o (ref {A}))
    (local.set $na (struct.get {L} $len (local.get $a)))
    (local.set $nb (struct.get {L} $len (local.get $b)))
    (local.set $o (array.new_default {A} (i32.add (local.get $na) (local.get $nb))))
    (array.copy {A} {A} (local.get $o) (i32.const 0) (struct.get {L} $data (local.get $a)) (i32.const 0) (local.get $na))
    (array.copy {A} {A} (local.get $o) (local.get $na) (struct.get {L} $data (local.get $b)) (i32.const 0) (local.get $nb))
    (struct.new {L} (i32.add (local.get $na) (local.get $nb)) (local.get $o)))"""
        elif op == "repeat":
            body = f"""(param $l (ref {L})) (param $k i32) (result (ref {L}))
    (local $n i32) (local $o (ref {A})) (local $d (ref {A})) (local $i i32) (local $total i64)
    (local.set $n {LEN})
    (if (i32.lt_s (local.get $k) (i32.const 0)) (then (local.set $k (i32.const 0))))
    (local.set $total (i64.mul (i64.extend_i32_u (local.get $n)) (i64.extend_i32_u (local.get $k))))
    (if (i64.gt_u (local.get $total) (i64.const 0x7FFFFFFF))
      (then (throw $snek.exc (call $MemoryError.__new__ (ref.null $str)))))
    (local.set $o (array.new_default {A} (i32.wrap_i64 (local.get $total))))
    (local.set $d {DATA})
    {loop("$i", "(local.get $k)", f"(array.copy {A} {A} (local.get $o) (i32.mul (local.get $i) (local.get $n)) (local.get $d) (i32.const 0) (local.get $n))")}
    (struct.new {L} (i32.wrap_i64 (local.get $total)) (local.get $o)))"""
        elif op == "imul":
            self.list_ops[m].add("repeat")
            body = f"""(param $l (ref {L})) (param $k i32)
    (local $r (ref {L}))
    (local.set $r (call {L}.repeat (local.get $l) (local.get $k)))
    (struct.set {L} $data (local.get $l) (struct.get {L} $data (local.get $r)))
    (struct.set {L} $len (local.get $l) (struct.get {L} $len (local.get $r))))"""
        elif op == "eq":
            body = f"""(param $a (ref null {L})) (param $b (ref null {L})) (result i32)
    (local $x (ref {L})) (local $y (ref {L})) (local $n i32) (local $i i32) (local $da (ref {A})) (local $db (ref {A}))
    (if (ref.eq (local.get $a) (local.get $b)) (then (return (i32.const 1))))
    (if (i32.or (ref.is_null (local.get $a)) (ref.is_null (local.get $b))) (then (return (i32.const 0))))
    (local.set $x (ref.as_non_null (local.get $a)))
    (local.set $y (ref.as_non_null (local.get $b)))
    (local.set $n (struct.get {L} $len (local.get $x)))
    (if (i32.ne (local.get $n) (struct.get {L} $len (local.get $y))) (then (return (i32.const 0))))
    (block $done (loop $next
      (br_if $done (i32.ge_u (local.get $i) (struct.get {L} $len (local.get $x))))
      (br_if $done (i32.ge_u (local.get $i) (struct.get {L} $len (local.get $y))))
      (local.set $da (struct.get {L} $data (local.get $x)))
      (local.set $db (struct.get {L} $data (local.get $y)))
      (br_if $done (i32.eqz {self.eq_code(e, load('$da', '$i'), load('$db', '$i'))}))
      (local.set $i (i32.add (local.get $i) (i32.const 1))) (br $next)))
    (if (result i32) (i32.or
          (i32.ge_u (local.get $i) (struct.get {L} $len (local.get $x)))
          (i32.ge_u (local.get $i) (struct.get {L} $len (local.get $y))))
      (then (i32.eq (struct.get {L} $len (local.get $x)) (struct.get {L} $len (local.get $y))))
      (else (i32.const 0))))"""
        else:
            raise AssertionError(f"list op {op}")
        return head + body

    def raise_code(self, cls_name: str, message: str) -> str:
        return f"(throw $snek.exc (call ${cls_name}.__new__ {self.lit(message)}))"

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
        oob = self.raise_code("IndexError", "list index out of range")
        oob_set = self.raise_code("IndexError", "list assignment index out of range")
        empty = self.raise_code("IndexError", "pop from empty list")
        code = f"""  (func $list.{m}.get (param $l (ref $list.{m})) (param $i i32) (result {R})
    (local $n i32)
    (local.set $n (struct.get $list.{m} $len (local.get $l)))
    (if (i32.lt_s (local.get $i) (i32.const 0)) (then (local.set $i (i32.add (local.get $i) (local.get $n)))))
    (if (i32.ge_u (local.get $i) (local.get $n)) (then {oob}))
    {load})
  (func $list.{m}.set (param $l (ref $list.{m})) (param $i i32) (param $v {R})
    (local $n i32)
    (local.set $n (struct.get $list.{m} $len (local.get $l)))
    (if (i32.lt_s (local.get $i) (i32.const 0)) (then (local.set $i (i32.add (local.get $i) (local.get $n)))))
    (if (i32.ge_u (local.get $i) (local.get $n)) (then {oob_set}))
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
    (if (i32.eqz (local.get $n)) (then {empty}))
    (local.set $n (i32.sub (local.get $n) (i32.const 1)))
    (local.set $d (struct.get $list.{m} $data (local.get $l)))
    (local.set $v ({get} $arr.{m} (local.get $d) (local.get $n))){clear}
    (struct.set $list.{m} $len (local.get $l) (local.get $n))
    {pop_val})"""
        if has_repr(e):
            item = self.repr_code(e, f"(call $list.{m}.get (local.get $l) (local.get $i))")
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
            self.heap(t)
            return f"(call $list.{mangle(t.elem)}.to_str {code})"
        if isinstance(t, (TupleT, DictT, DictViewT)):
            return f"(call {self.collection_op(t, 'to_str')} {code})"
        if isinstance(t, OptionalT):
            return self.optional_repr_code(t, code)
        if isinstance(t, ClassT):
            return self.class_str(t, code)
        raise AssertionError(f"to_str of {t}")

    def repr_code(self, t: Type, code: str) -> str:
        """repr() as used for list elements: strings are quoted, None prints as None."""
        if t == STR:
            return f"(call $rt.str_repr {code})"
        if t == OptionalT(STR):
            return f"(call $rt.opt_str_repr {code})"
        if isinstance(t, OptionalT):
            return self.optional_repr_code(t, code)
        return self.to_str_code(t, code)

    def string_builder_helpers(self) -> str:
        """Specialize mixed string/integer concatenations without allocating their parts.

        Call arguments evaluate each expression (including custom __str__) once,
        left to right. Only primitive integer formatting is delayed: it has no
        user-visible side effects. The helper allocates the final bytes + header.
        """
        helpers = []
        for shape in sorted(self.string_builders):
            params = " ".join(f"(param $p{i} {'i64' if kind == 'i' else '(ref $str)'})"
                              for i, kind in enumerate(shape))
            lines = [f"  (func $str.build.{shape} {params} (result (ref $str))",
                     "    (local $len i32) (local $cp i32) (local $pos i32) (local $out (ref $u8s))"]
            for i, kind in enumerate(shape):
                lines.append(f"    (local $n{i} i32)" + (f" (local $b{i} (ref $u8s))" if kind == 's' else ""))
            for i, kind in enumerate(shape):
                if kind == 'i':
                    length = f"(call $rt.i64_str_len (local.get $p{i}))"
                    cp = f"(local.get $n{i})"
                else:
                    lines.append(f"    (local.set $b{i} (struct.get $str $bytes (local.get $p{i})))")
                    length = f"(array.len (local.get $b{i}))"
                    cp = f"(struct.get $str $cp (local.get $p{i}))"
                lines += [f"    (local.set $n{i} {length})",
                          f"    (local.set $len (i32.add (local.get $len) (local.get $n{i})))",
                          f"    (local.set $cp (call $rt.cp_sum (local.get $cp) {cp}))"]
            lines.append("    (local.set $out (array.new_default $u8s (local.get $len)))")
            for i, kind in enumerate(shape):
                if kind == 'i':
                    lines.append(f"    (local.set $pos (call $rt.i64_write (local.get $out) (local.get $pos) "
                                 f"(local.get $p{i}) (local.get $n{i})))")
                else:
                    lines += [f"    (array.copy $u8s $u8s (local.get $out) (local.get $pos) "
                              f"(local.get $b{i}) (i32.const 0) (local.get $n{i}))",
                              f"    (local.set $pos (i32.add (local.get $pos) (local.get $n{i})))"]
            lines.append("    (call $rt.mkstr (local.get $out) (local.get $cp)))")
            helpers.append("\n".join(lines))
        return "\n".join(helpers)

    # ---- functions -------------------------------------------------------------

    def emit_func(self, f: ir.Func) -> str:
        if f.closure_type is not None:
            f.sig_type = self.heap(f.closure_type)[1:] + ".sig"
        if f.machine:
            return self.emit_machine(f)
        self.fn = FnState(f)
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
        lines += indent(body, 4)
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
        if isinstance(s, (ir.CellSet, ir.CellClear)):
            return self.closure_stmt(s)
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
            return self.jump("return", self.ex(s.value) if s.value is not None else None)
        if isinstance(s, ir.If):
            out = [f"(if {self.ex(s.cond)}", "  (then"]
            out += closed(indent(self.block(s.then), 4))
            if s.else_:
                out.append("  (else")
                out += closed(indent(self.block(s.else_), 4))
            out[-1] += ")"
            return out
        if isinstance(s, ir.Loop):
            n = fn.label()
            brk, cont, top = f"$brk.{n}", f"$cont.{n}", f"$top.{n}"
            fn.ctl.append(LoopCtl(brk, cont))
            out = [f"(block {brk}", f"  (loop {top}"]
            if s.cond is not None:
                out.append(f"    (br_if {brk} (i32.eqz {self.ex(s.cond)}))")
            out.append(f"    (block {cont}")
            out += closed(indent(self.block(s.body), 6))
            fn.ctl.pop()
            out += indent(self.block(s.step), 4)
            out.append(f"    (br {top})))")
            return out
        if isinstance(s, ir.InlineBlock):
            n = fn.label()
            fn.ctl.append(InlineCtl(f"$inl.{n}"))
            out = [f"(block $inl.{n}"] + closed(indent(self.block(s.body)))
            fn.ctl.pop()
            return out
        if isinstance(s, ir.ExitInline):
            return self.jump("exit_inline")
        if isinstance(s, ir.Break):
            return self.jump("break")
        if isinstance(s, ir.Continue):
            return self.jump("continue")
        if isinstance(s, ir.Print):
            # CPython evaluates every argument before writing anything, so an argument with
            # side effects (or one that raises) must not run after earlier ones were printed.
            # str() itself runs at write time, so a list argument shows mutations made by later
            # arguments: the unconverted value is what gets saved.
            out, codes = [], []
            for a in s.args:
                if len(s.args) == 1 or self.order_free(a):
                    codes.append(self.ex(a))
                    continue
                v = a.operand if isinstance(a, ir.ToStr) else a
                tmp = fn.temp(self.storage(v.ty))
                out.append(f"(local.set {tmp} {self.ex(v)})")
                read = f"(local.get {tmp})"
                if v.ty.is_ref and not isinstance(v.ty, OptionalT):
                    read = f"(ref.as_non_null {read})"
                codes.append(self.to_str_code(v.ty, read) if isinstance(a, ir.ToStr) else read)
            for i, code in enumerate(codes):
                if i:
                    out.append(self.out_text(s.sep))
                out.append(f"(call $rt.out_str {code})")
            if s.end:
                out.append(self.out_text(s.end))
            return [line for line in out if line]
        if isinstance(s, ir.Raise):
            if s.exc is None:
                return [f"(throw $snek.exc (ref.as_non_null (local.get ${s.reraise})))"]
            return [f"(throw $snek.exc {self.ex(s.exc)})"]
        if isinstance(s, ir.Try):
            return self.try_finally(s) if s.finally_ is not None else self.try_except(s)
        raise AssertionError(f"unknown stmt {s}")

    def order_free(self, e: ir.Expr) -> bool:
        """True if evaluating e later gives the same result and cannot raise. Locals qualify
        because nothing else can assign them (there are no closures)."""
        if isinstance(e, ir.ToStr):
            return self.order_free(e.operand)
        return isinstance(e, (ir.Const, ir.StrLit, ir.NoneLit, ir.LocalGet))

    def out_text(self, text: str) -> str:
        data = text.encode()
        if not data:
            return ""
        if len(data) == 1:
            return f"(call $rt.out_byte (i32.const {data[0]}))"
        return f"(call $rt.out_str {self.lit(text)})"

    # ---- control transfer and exceptions ---------------------------------------

    def jump(self, kind: str, value: str | None = None) -> list[str]:
        """Emit return/break/continue. Leaving a try/finally region does not jump directly:
        it records a pending action, branches to the region's finally body, and the region
        re-issues the jump after the finally body has run (possibly through outer regions)."""
        fn = self.fn
        assert fn is not None
        for c in reversed(fn.ctl):
            if isinstance(c, FinallyCtl):
                c.used.add(kind)
                out = []
                if kind == "return" and value is not None:
                    if c.ret is None:
                        c.ret = fn.temp(self.storage(fn.f.ret))
                    out.append(f"(local.set {c.ret} {value})")
                return out + [f"(local.set {c.k} (i32.const {JUMP_CODES[kind]}))", f"(br {c.exit})"]
            if isinstance(c, LoopCtl) and kind in ("break", "continue"):
                return [f"(br {c.brk if kind == 'break' else c.cont})"]
            if isinstance(c, InlineCtl) and kind == "exit_inline":
                return [f"(br {c.exit})"]
        assert kind == "return", f"{kind} outside a loop"
        return [f"(return {value})" if value is not None else "(return)"]

    def try_except(self, s: ir.Try) -> list[str]:
        """
        (block $try.N
          (local.set $exc
            (block $catch.N (result (ref $BaseException))
              (try_table (catch $snek.exc $catch.N) <body>)
              <else>
              (br $try.N)))
          (if (ref.test (ref $ValueError) (local.get $exc)) (then <handler> (br $try.N)))
          ...
          (throw $snek.exc (local.get $exc)))     ;; no handler matched: propagate
        """
        fn = self.fn
        assert fn is not None
        n = fn.label()
        exc = f"${s.exc_local}"
        out = [f"(block $try.{n}",
               f"  (local.set {exc}",
               f"    (block $catch.{n} (result (ref {EXC}))",
               f"      (try_table (catch $snek.exc $catch.{n})"]
        out += closed(indent(self.block(s.body), 8))
        out += indent(self.block(s.else_), 6)
        out.append(f"      (br $try.{n})))")
        catch_all = False
        for h in s.handlers:
            hb: list[str] = []
            if h.var:
                src = f"(local.get {exc})"
                heap = self.heap(h.var_decl)  # type: ignore[arg-type]
                if h.var in fn.nonnull:  # a parameter (non-nullable local) reused as the name
                    src = f"(ref.cast (ref {heap}) {src})"
                elif h.var_decl != ClassT(self.exc_info()):  # declared with a narrower class
                    src = f"(ref.cast (ref null {heap}) {src})"
                hb.append(f"(local.set ${h.var} {src})")
            hb += self.block(h.body)
            hb.append(f"(br $try.{n})")
            if h.classes is None:
                catch_all = True
                out += indent(hb)
                break
            tests = [f"(ref.test (ref ${c.name}) (local.get {exc}))" for c in h.classes]
            test = tests[0]
            for t in tests[1:]:
                test = f"(i32.or {test} {t})"
            out.append(f"  (if {test}")
            out.append("    (then")
            out += closed(closed(indent(hb, 6)))
        if not catch_all:
            out.append(f"  (throw $snek.exc (ref.as_non_null (local.get {exc})))")
        out[-1] += ")"
        return out

    def try_finally(self, s: ir.Try) -> list[str]:
        """
        (local.set $k (i32.const 0))
        (block $fin.N                                ;; every way out of the region lands here
          (local.set $fin.exc
            (block $fin.catch.N (result (ref $BaseException))
              (try_table (catch $snek.exc $fin.catch.N)
                <try/except/else; return/break/continue set $k and br $fin.N>)
              (br $fin.N)))
          (local.set $k (i32.const -1)))             ;; an exception escaped the region
        <finally body>                               ;; emitted once, outside the try_table
        (if (k == -1) (then (throw $snek.exc $fin.exc)))
        (if (k == 1) (then <return $ret>)) ...       ;; re-issued in the enclosing context

        The finally body sits outside the try_table, so an exception raised inside it
        propagates normally instead of re-entering this handler.
        """
        fn = self.fn
        assert fn is not None
        n = fn.label()
        k = fn.temp("i32")
        exc = fn.temp(f"(ref null {EXC})")
        ctl = FinallyCtl(exit=f"$fin.{n}", k=k)
        fn.ctl.append(ctl)
        inner = self.try_except(s) if s.handlers else self.block(s.body)
        fn.ctl.pop()
        out = [f"(local.set {k} (i32.const {FALLTHROUGH}))",
               f"(block $fin.{n}",
               f"  (local.set {exc}",
               f"    (block $fin.catch.{n} (result (ref {EXC}))",
               f"      (try_table (catch $snek.exc $fin.catch.{n})"]
        out += closed(indent(inner, 8))
        out.append(f"      (br $fin.{n})))")
        out.append(f"  (local.set {k} (i32.const {EXCEPTION})))")
        out += self.block(s.finally_ or [])
        out.append(f"(if (i32.eq (local.get {k}) (i32.const {EXCEPTION})) "
                   f"(then (throw $snek.exc (ref.as_non_null (local.get {exc})))))")
        for kind in JUMP_CODES:
            if kind not in ctl.used:
                continue
            value = None
            if kind == "return" and ctl.ret is not None:
                value = f"(local.get {ctl.ret})"
                if fn.f.ret.is_ref and not isinstance(fn.f.ret, OptionalT):
                    value = f"(ref.as_non_null {value})"
            out.append(f"(if (i32.eq (local.get {k}) (i32.const {JUMP_CODES[kind]}))")
            out.append("  (then")
            out += closed(closed(indent(self.jump(kind, value), 4)))
        return out

    def exc_info(self) -> ClassInfo:
        return next(c for c in self.m.classes if c.name == "BaseException")

    # ---- expressions -----------------------------------------------------------

    def ex(self, e: ir.Expr) -> str:
        if isinstance(e, (ir.FunctionRef, ir.Closure, ir.CallValue, ir.CellNew, ir.CellGet, ir.EnvGet)):
            return self.closure_expr(e)
        if isinstance(e, ir.MachineOp):
            return self.machine_op(e)
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
                    return f"(ref.cast (ref {self.heap(e.ty)}) {code})"  # narrowed by isinstance/except
                if e.name not in fn.nonnull:
                    return f"(ref.as_non_null {code})"
            return code
        if isinstance(e, ir.ObjectEq):
            return self.object_eq(e.left.ty, e.right.ty, self.ex(e.left), self.ex(e.right))
        if isinstance(e, ir.ClassRef):
            return f"(global.get ${e.cls.name}.vt.inst)"
        if isinstance(e, ir.TypeOf):
            return f"(struct.get ${e.operand.ty.info.name} $__vt__ {self.ex(e.operand)})"
        if isinstance(e, ir.ClassName):
            return f"(struct.get ${e.operand.ty.info.name}.vt $__name__ {self.ex(e.operand)})"
        if isinstance(e, ir.TracebackToken):
            return "(struct.new $traceback<>)"
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
            key = (e.operand.ty, e.ty)
            inner = self.ex(e.operand)
            if key in CHECKED_TRUNC:
                if e.operand.ty == F32:
                    inner = f"(f64.promote_f32 {inner})"
                return f"(call {CHECKED_TRUNC[key]} {inner})"  # type: ignore[index]
            op = CONVERSIONS[key]  # type: ignore[index]
            return f"({op} {inner})" if op else inner
        if isinstance(e, ir.IfExpr):
            return (f"(if (result {self.valtype(e.ty)}) {self.ex(e.cond)} "
                    f"(then {self.ex(e.then)}) (else {self.ex(e.else_)}))")
        if isinstance(e, ir.Call):
            if e.func in ("snek.wait_all", "snek.gather"):
                if self.component:
                    raise ValueError(e.func[5:] + "() is not yet supported for components")
                heaps = [self.heap(arg.ty) for arg in e.args]
                name = "$" + e.func + "<" + "~".join(h[1:] for h in heaps) + ">"
                self.wait_groups[name] = heaps
                if e.func == "snek.gather":
                    values = [self.machine_value(arg.ty, f"(ref.as_non_null (struct.get {name}.frame $child{i} (local.get $f)))") for i, arg in enumerate(e.args)]
                    result = e.ty.elem
                    heap = self.heap(result)
                    code = f"(struct.new {heap} (i32.const {len(values)}) (array.new_fixed $arr.{mangle(result.elem)} {len(values)} {' '.join(values)}))"
                    self.wait_results[name] = [self.heap(e.ty), heap, code]
                return self.call(name, e.args)
            if e.func.startswith("snek.lib."):
                from .wasi_bindings import OPERATIONS
                self.wasi_features.add(OPERATIONS[e.func.removeprefix("snek.lib.")])
                if isinstance(e.ty, ListT):
                    self.heap(e.ty)
            if e.func.startswith("snek.sys."):
                self.stdlib_io.add(e.func.removeprefix("snek.sys."))
                self.heap(ListT(I32))
            if e.func in ("snek.read_bytes", "snek.write_bytes"):
                if self.component:
                    from .errors import CompileError
                    raise CompileError("read_bytes/write_bytes currently require the core WASI target, not --component")
                self.byte_io = True
                self.heap(ListT(I32))
            return self.call(f"${e.func}", e.args)
        if isinstance(e, ir.New):
            return self.call(f"${e.cls.name}.__new__", e.args)
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
        if isinstance(e, ir.TupleLit):
            return f"(struct.new {self.heap(e.ty)} {' '.join(self.ex(i) for i in e.items)})"
        if isinstance(e, ir.TupleGet):
            return f"(struct.get {self.heap(e.obj.ty)} {e.index} {self.ex(e.obj)})"
        if isinstance(e, (ir.TupleOp, ir.DictOp)):
            t = strip_optional(e.obj.ty)
            return self.call(self.collection_op(t, e.op), [e.obj, *e.args])
        if isinstance(e, ir.DictLit):
            D = self.heap(e.ty)
            tmp = fn.temp(f"(ref null {D})")
            new, put = self.collection_op(e.ty, "new"), self.collection_op(e.ty, "set")
            # Python evaluates all key/value expressions before populating a literal.
            pairs = []
            code = []
            for k, v in e.items:
                kt, vt = fn.temp(self.storage(k.ty)), fn.temp(self.storage(v.ty))
                code += [f"(local.set {kt} {self.ex(k)})", f"(local.set {vt} {self.ex(v)})"]
                pairs.append((self.local_value(k.ty, kt), self.local_value(v.ty, vt)))
            code.append(f"(local.set {tmp} (call {new}))")
            code += [f"(call {put} (ref.as_non_null (local.get {tmp})) {k} {v})" for k, v in pairs]
            return f"(block (result (ref {D})) {' '.join(code)} (ref.as_non_null (local.get {tmp})))"
        if isinstance(e, ir.ListGet):
            self.heap(e.lst.ty)
            return f"(call $list.{mangle(e.ty)}.get {self.ex(e.lst)} {self.ex(e.index)})"
        if isinstance(e, ir.ListLen):
            return f"(struct.get {self.heap(e.lst.ty)} $len {self.ex(e.lst)})"
        if isinstance(e, ir.ListPop):
            self.heap(e.lst.ty)
            return f"(call $list.{mangle(e.ty)}.pop {self.ex(e.lst)})"
        if isinstance(e, ir.ListOp):
            t = strip_optional(e.lst.ty)
            assert isinstance(t, ListT)
            return self.call(self.list_op(t, e.op), [e.lst, *e.args])
        if isinstance(e, ir.KeyedListOp):
            name = f"$list.{mangle(e.lst.ty.elem)}.{e.op}.key<{mangle(e.key.ty)}>"
            self.heap(e.lst.ty)
            self.heap(e.key.ty)
            self.keyed_builtins[name] = (e.lst.ty.elem, e.key.ty, e.op, e.ty)
            return self.call(name, [e.lst, e.key, *e.args])
        if isinstance(e, ir.ListAppend):
            m = mangle(e.lst.ty.elem)  # type: ignore[attr-defined]
            self.heap(e.lst.ty)
            return f"(call $list.{m}.append {self.ex(e.lst)} {self.ex(e.value)})"
        if isinstance(e, ir.StrLen):
            return f"(call $rt.str_len {self.ex(e.s)})"
        if isinstance(e, ir.StrConcat):
            # Flatten a + b + c (and f-strings, which lower to concat chains) into one
            # n-ary concat: one allocation instead of one per `+`. Parts stay in order.
            parts: list[ir.Expr] = []
            stack: list[ir.Expr] = [e]
            while stack:
                x = stack.pop()
                if isinstance(x, ir.StrConcat):
                    stack += [x.right, x.left]
                else:
                    parts.append(x)
            if any(isinstance(p, ir.ToStr) and p.operand.ty in (I32, I64) for p in parts):
                # Fuse only plain decimal integer conversions. Other formatting
                # (bool, float, specs, __str__) still runs at its original position.
                shape, args = "", []
                for p in parts:
                    if isinstance(p, ir.ToStr) and p.operand.ty in (I32, I64):
                        shape += "i"
                        code = self.ex(p.operand)
                        args.append(f"(i64.extend_i32_s {code})" if p.operand.ty == I32 else code)
                    else:
                        shape += "s"
                        args.append(self.ex(p))
                self.string_builders.add(shape)
                return f"(call $str.build.{shape} {' '.join(args)})"
            if len(parts) == 2:
                return f"(call $rt.str_concat {self.ex(parts[0])} {self.ex(parts[1])})"
            codes = "".join(" " + self.ex(p) for p in parts)
            return f"(call $rt.str_concat_n (array.new_fixed $strs {len(parts)}{codes}))"
        if isinstance(e, ir.ToStr):
            if isinstance(e.operand.ty, ListT):
                self.heap(e.operand.ty)
            return self.to_str_code(e.operand.ty, self.ex(e.operand))
        if isinstance(e, ir.TypeName):
            c = e.cls.name
            return f"(struct.get ${c}.vt $__name__ (struct.get ${c} $__vt__ {self.ex(e.operand)}))"
        if isinstance(e, ir.Seq):
            result = f" (result {self.valtype(e.ty)})" if e.ty != NONE else ""
            stmts = " ".join(self.block(e.stmts))
            return f"(block{result} {stmts} {self.ex(e.value)})"
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
            # Preserve the IR's exact f64 value. A shortest decimal spelling at
            # an f32 midpoint can fall on the other side of the rounding tie.
            lit = fv.hex()
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
        if e.op in ("min", "max"):
            a, b = self.fn.temp(n), self.fn.temp(n)  # type: ignore[union-attr]
            cmp = ("lt" if e.op == "min" else "gt") + ("_s" if t.is_int else "")
            # Return the first operand on ties or an unordered (NaN) comparison.
            return (f"(block (result {n}) (local.set {a} {l}) (local.set {b} {r}) "
                    f"(select (local.get {b}) (local.get {a}) ({n}.{cmp} (local.get {b}) (local.get {a}))))")
        if e.op in SIMPLE_BIN:
            return f"({n}.{SIMPLE_BIN[e.op]} {l} {r})"
        if t.is_int:
            if e.op in ("floordiv", "mod"):
                return f"(call $rt.{n}.{e.op} {l} {r})"
        else:
            if e.op == "div_int":
                return f"(call $rt.f64.div_int {l} {r})"
            if e.op in ("div", "floordiv", "mod"):
                if t == F64:
                    return f"(call $rt.f64.{e.op} {l} {r})"
                return f"(f32.demote_f64 (call $rt.f64.{e.op} (f64.promote_f32 {l}) (f64.promote_f32 {r})))"
        raise AssertionError(f"binary {e.op} on {t}")

    def compare(self, e: ir.Compare) -> str:
        l, r = self.ex(e.left), self.ex(e.right)
        t = e.operand_ty
        if e.op in ("is", "isnot"):
            code = f"(call $__snek_object_same {l} {r})" if strip_optional(t) == HOST_OBJECT else f"(ref.eq {l} {r})"
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


def emit_module(module: ir.Module, component: bool = False, *, graph_factory=None, machine_backend=None,
                exports: list[str] | None = None, entry: str = "") -> str:
    # Explicit opt-in while the rest of the emitter remains Python. A missing
    # or failing selected suspension pass is an error, never a Python fallback.
    import os
    if graph_factory is None and os.environ.get("SNEK_MACHINE_LOWERER"):
        from .machine_bridge import WasmGraphFactory
        graph_factory = WasmGraphFactory(Path(os.environ["SNEK_MACHINE_LOWERER"]))
    if machine_backend is None and os.environ.get("SNEK_MACHINE_EMITTER"):
        from .machine_bridge import WasmMachineBackend
        machine_backend = WasmMachineBackend(Path(os.environ["SNEK_MACHINE_EMITTER"]))
    selected = []
    from bootstrap.library import LibraryExport, library_code, library_target, library_host_imports, library_host_wrapper, library_callbacks, library_callback_imports, library_callback_async
    hosts = [LibraryExport(f.host, f.name, [str(t) for _, t in f.params],
                          str(f.ret.elem) if isinstance(f.ret, CoroutineT) else str(f.ret),
                          "$" + mangle(f.ret) if isinstance(f.ret, CoroutineT) else "")
             for f in module.funcs if f.host]
    if hosts and (not exports or component):
        raise ValueError("host imports require a --library build")
    if exports:
        for name in exports:
            target = library_target(name, entry)
            function = next((f for f in module.funcs if f.name == (target if entry else name)), None)
            if function is None:
                raise ValueError("library export must name a function defined in the entry module: " + name)
            if function.machine and not isinstance(function.ret, CoroutineT):
                raise ValueError("library exports cannot be generators: " + name)
            selected.append(LibraryExport(name, function.name, [str(t) for _, t in function.params],
                                          str(function.ret.elem) if isinstance(function.ret, CoroutineT) else str(function.ret),
                                          "$" + mangle(function.ret) if isinstance(function.ret, CoroutineT) else ""))
        if any("str" in item.params for item in selected) or any(item.ret == "str" or item.heap for item in hosts) or library_callbacks(selected, hosts):
            # Do not mutate the checked IR: command emission can reuse it.
            module = replace(module, unicode=replace(module.unicode, dynamic=True))
    emitter = Emitter(module, component=component, graph_factory=graph_factory, machine_backend=machine_backend)
    wat = emitter.emit()
    if emitter.host_objects and not exports:
        raise ValueError("HostObject requires a --library build")
    if exports:
        if component or emitter.main_async or emitter.byte_io or emitter.stdlib_io or emitter.wasi_features:
            raise ValueError("library targets support print(), not WASI services or async initialization")
        wat = wat.replace('(export "_start")', '(export "__snek_init")')
        callbacks = library_callbacks(selected, hosts)
        imports = library_host_imports(hosts, emitter.host_objects, library_callback_async(callbacks)) + "\n" + library_callback_imports(callbacks, len(hosts))
        wat = wat.replace("(module\n", "(module\n" + imports + "\n", 1)
        wrappers = "\n".join(library_host_wrapper(item, i) for i, item in enumerate(hosts))
        wat = wat.rstrip()[:-1] + wrappers + "\n" + library_code(selected, hosts) + "\n)\n"
    return wat
