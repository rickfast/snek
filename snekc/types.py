"""Type representations.

Numbers are machine types (i32, i64, f32, f64) and are never boxed. Every
other value is a reference: language objects use WasmGC structs, while opaque
HostObject values use external references. Only references can be optional (`T | None`).
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field


class Type:
    is_ref = False


@dataclass(frozen=True)
class Prim(Type):
    name: str

    def __str__(self) -> str:
        return self.name

    @property
    def is_int(self) -> bool:
        return self.name in ("i32", "i64")

    @property
    def is_float(self) -> bool:
        return self.name in ("f32", "f64")

    @property
    def is_numeric(self) -> bool:
        return self.is_int or self.is_float


I32 = Prim("i32")
I64 = Prim("i64")
F32 = Prim("f32")
F64 = Prim("f64")
BOOL = Prim("bool")
NUMERIC = {"i32": I32, "i64": I64, "f32": F32, "f64": F64}


@dataclass(frozen=True)
class NoneT(Type):
    def __str__(self) -> str:
        return "None"


NONE = NoneT()


@dataclass(frozen=True)
class StrT(Type):
    is_ref = True

    def __str__(self) -> str:
        return "str"


STR = StrT()


@dataclass(frozen=True)
class HostObjectT(Type):
    """An opaque, GC-managed JavaScript object reference (library target only)."""
    is_ref = True

    def __str__(self) -> str:
        return "HostObject"


HOST_OBJECT = HostObjectT()


@dataclass(frozen=True)
class StreamT(Type):
    """A typed view of a CLI stream; views share a closeable resource state."""
    kind: str  # TextInput | TextOutput | BinaryInput | BinaryOutput
    is_ref = True

    def __str__(self) -> str:
        return f"io.{self.kind}"


@dataclass(frozen=True)
class BufT(Type):
    """A growable UTF-8 byte buffer; only the built-in str methods (builtins.py) can name it."""

    is_ref = True

    def __str__(self) -> str:
        return "__Buf"


BUF = BufT()


@dataclass(frozen=True)
class ListT(Type):
    elem: Type
    is_ref = True

    def __str__(self) -> str:
        return f"list[{self.elem}]"


@dataclass(frozen=True)
class CallableT(Type):
    params: tuple[Type, ...]
    ret: Type
    is_ref = True

    def __str__(self) -> str:
        return f"Callable[[{', '.join(map(str, self.params))}], {self.ret}]"


def host_boundary(t: Type) -> bool:
    leaf = (I32, I64, F32, F64, BOOL, STR, HOST_OBJECT, OptionalT(HOST_OBJECT))
    if isinstance(t, ListT): return host_boundary(t.elem) if isinstance(t.elem, ListT) else t.elem in leaf
    if isinstance(t, CallableT):
        ret = t.ret.elem if isinstance(t.ret, CoroutineT) else t.ret
        return (all(p in leaf or (isinstance(p, ListT) and host_boundary(p)) for p in t.params)
                and (ret in (*leaf, NONE) or (isinstance(ret, ListT) and host_boundary(ret))))
    return t in (*leaf, NONE)


@dataclass(frozen=True)
class CellT(Type):
    """Compiler-only shared lexical binding; the payload remains unboxed."""
    elem: Type
    is_ref = True


@dataclass(frozen=True)
class EnvT(Type):
    """Compiler-only closure environment; None denotes the erased base type."""
    captures: tuple[Type, ...] | None = None
    is_ref = True


@dataclass(frozen=True)
class IteratorT(Type):
    elem: Type
    is_ref = True

    def __str__(self) -> str:
        return f"Iterator[{self.elem}]"


@dataclass(frozen=True)
class CoroutineT(Type):
    elem: Type
    is_ref = True

    def __str__(self) -> str:
        return f"Coroutine[None, None, {self.elem}]"


@dataclass(frozen=True)
class TupleT(Type):
    elems: tuple[Type, ...]
    is_ref = True

    def __str__(self) -> str:
        return "tuple[" + (", ".join(map(str, self.elems)) or "()") + "]"


@dataclass(frozen=True)
class DictT(Type):
    key: Type
    value: Type
    is_ref = True

    def __str__(self) -> str:
        return f"dict[{self.key}, {self.value}]"


@dataclass(frozen=True)
class DictViewT(Type):
    mapping: DictT
    kind: str  # keys | values | items
    is_ref = True

    @property
    def elem(self) -> Type:
        if self.kind == "keys":
            return self.mapping.key
        if self.kind == "values":
            return self.mapping.value
        return TupleT((self.mapping.key, self.mapping.value))

    def __str__(self) -> str:
        return f"{self.mapping}.{self.kind}()"


def is_hashable(t: Type) -> bool:
    """Keys have value equality and a stable hash; mutable containers cannot be keys.

    Floats are excluded for now: unboxed NaNs cannot preserve Python's identity
    shortcut. Object keys await __hash__ support.
    """
    if isinstance(t, OptionalT):
        return is_hashable(t.inner)
    if isinstance(t, TupleT):
        return all(is_hashable(e) for e in t.elems)
    return t in (STR, BOOL, I32, I64)


def has_repr(t: Type) -> bool:
    if isinstance(t, OptionalT):
        return has_repr(t.inner)
    if isinstance(t, ListT):
        return has_repr(t.elem)
    if isinstance(t, TupleT):
        return all(has_repr(e) for e in t.elems)
    if isinstance(t, DictT):
        return has_repr(t.key) and has_repr(t.value)
    if isinstance(t, DictViewT):
        return has_repr(t.elem)
    return isinstance(t, Prim) or t == STR


@dataclass(frozen=True)
class ClassT(Type):
    info: ClassInfo
    is_ref = True

    def __str__(self) -> str:
        return self.info.name


@dataclass(frozen=True)
class TypeT(Type):
    """A class value, represented by its immutable vtable."""
    info: ClassInfo
    is_ref = True

    def __str__(self) -> str:
        return f"type[{self.info.name}]"


@dataclass(frozen=True)
class TracebackT(Type):
    """Opaque exception-path token; frames are not exposed."""
    is_ref = True

    def __str__(self) -> str:
        return "TracebackType"


TRACEBACK = TracebackT()


@dataclass(frozen=True)
class OptionalT(Type):
    inner: Type  # always a reference type
    is_ref = True

    def __str__(self) -> str:
        return f"{self.inner} | None"


# ---- declarations ---------------------------------------------------------


@dataclass
class FieldInfo:
    name: str
    ty: Type
    final: bool
    default: ast.expr | None
    owner: ClassInfo
    index: int  # struct field index; 0 is the vtable


@dataclass
class FuncSig:
    params: list[tuple[str, Type]]
    ret: Type
    defaults: list[ast.expr | None] = field(default_factory=list)  # one per param; literals only
    posonly: int = 0  # leading parameters that cannot be passed by keyword (built-in methods)
    host: str = ""

    def __post_init__(self) -> None:
        if not self.defaults:
            self.defaults = [None] * len(self.params)

    def same_as(self, other: FuncSig) -> bool:
        """Exact match, as required for overrides: callers bind arguments by name and fill
        defaults from the static receiver type, so names and defaults must agree too."""
        dump = lambda ds: [ast.dump(d) if d is not None else None for d in ds]  # noqa: E731
        return (self.ret == other.ret and self.params == other.params
                and dump(self.defaults) == dump(other.defaults))


@dataclass
class MethodInfo:
    name: str
    sig: FuncSig
    decl: ClassInfo  # class that introduced the vtable slot
    slot: int
    node: ast.FunctionDef | ast.AsyncFunctionDef
    owner: ClassInfo  # class whose body holds this implementation


@dataclass(eq=False)
class ClassInfo:
    name: str
    node: ast.ClassDef
    base: ClassInfo | None = None
    fields: dict[str, FieldInfo] = field(default_factory=dict)  # inherited first
    methods: dict[str, MethodInfo] = field(default_factory=dict)  # slot order
    subclasses: list[ClassInfo] = field(default_factory=list)
    init_node: ast.FunctionDef | None = None
    builtin: bool = False  # defined by snekc/builtins.py (the exception hierarchy)

    @property
    def is_final(self) -> bool:
        return not self.subclasses

    def is_subclass_of(self, other: ClassInfo) -> bool:
        c: ClassInfo | None = self
        while c is not None:
            if c is other:
                return True
            c = c.base
        return False

    def all_subclasses(self) -> list[ClassInfo]:
        out: list[ClassInfo] = []
        for s in self.subclasses:
            out.append(s)
            out.extend(s.all_subclasses())
        return out

    def effective_init(self) -> tuple[ast.FunctionDef, ClassInfo] | None:
        c: ClassInfo | None = self
        while c is not None:
            if c.init_node is not None:
                return c.init_node, c
            c = c.base
        return None


# ---- relations ------------------------------------------------------------

# Implicit conversions that never lose information.
WIDEN = {(I32, I64), (I32, F64), (F32, F64)}


def join_numeric(a: Type, b: Type) -> Prim | None:
    if not (isinstance(a, Prim) and isinstance(b, Prim) and a.is_numeric and b.is_numeric):
        return None
    if a == b:
        return a
    if (a, b) in WIDEN:
        return b
    if (b, a) in WIDEN:
        return a
    return None


def strip_optional(t: Type) -> Type:
    return t.inner if isinstance(t, OptionalT) else t


def is_assignable(src: Type, dst: Type) -> bool:
    if src == dst:
        return True
    if isinstance(src, (ClassT, TypeT)) and type(src) is type(dst):
        return src.info.is_subclass_of(dst.info)
    if isinstance(dst, OptionalT):
        if src == NONE:
            return True
        return is_assignable(strip_optional(src), dst.inner)
    return False


def common_class(a: ClassInfo, b: ClassInfo) -> ClassInfo | None:
    c: ClassInfo | None = a
    while c is not None:
        if b.is_subclass_of(c):
            return c
        c = c.base
    return None


def join_types(a: Type, b: Type) -> Type | None:
    """Least common type of two branches/elements, or None."""
    if a == b:
        return a
    if (n := join_numeric(a, b)) is not None:
        return n
    if a == NONE and b.is_ref:
        return b if isinstance(b, OptionalT) else OptionalT(b)
    if b == NONE and a.is_ref:
        return a if isinstance(a, OptionalT) else OptionalT(a)
    sa, sb = strip_optional(a), strip_optional(b)
    optional = isinstance(a, OptionalT) or isinstance(b, OptionalT)
    joined: Type | None = None
    if sa == sb:
        joined = sa
    elif isinstance(sa, (ClassT, TypeT)) and type(sa) is type(sb):
        c = common_class(sa.info, sb.info)
        joined = type(sa)(c) if c else None
    if joined is None:
        return None
    return OptionalT(joined) if optional else joined
