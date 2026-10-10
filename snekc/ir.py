"""Typed intermediate representation.

A small tree IR that sits between the checker and the WAT emitter. Every
expression carries its resolved type, every implicit conversion is explicit,
and Python-level sugar (for loops, augmented assignment, f-strings, __init__)
has already been lowered. The emitter only decides *how* to encode things in
Wasm (direct vs virtual calls, packed arrays, null checks), never *what* they
mean. A binary encoder or a different back end would consume this same IR.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .types import ClassInfo, FieldInfo, MethodInfo, Type

# ---- expressions ------------------------------------------------------------


@dataclass
class Expr:
    ty: Type


@dataclass
class Const(Expr):
    value: int | float | bool


@dataclass
class StrLit(Expr):
    text: str


@dataclass
class NoneLit(Expr):
    """ty is the OptionalT the null is typed as."""


@dataclass
class LocalGet(Expr):
    name: str
    declared: Type  # ty may be narrower (smart cast after `is not None` / isinstance)


@dataclass
class Unary(Expr):
    op: str  # neg | not | invert | abs
    operand: Expr


@dataclass
class Binary(Expr):
    op: str  # add sub mul div floordiv mod bitand bitor bitxor shl shr min max
    left: Expr
    right: Expr


@dataclass
class Compare(Expr):
    op: str  # eq ne lt le gt ge is isnot
    operand_ty: Type
    left: Expr
    right: Expr


@dataclass
class IsNull(Expr):
    operand: Expr
    negate: bool


@dataclass
class IsInstance(Expr):
    operand: Expr
    cls: ClassInfo


@dataclass
class Logical(Expr):
    op: str  # and | or
    left: Expr
    right: Expr


@dataclass
class Convert(Expr):
    """Numeric conversion from operand.ty to ty."""

    operand: Expr


@dataclass
class IfExpr(Expr):
    cond: Expr
    then: Expr
    else_: Expr
    mixed_int: bool = False  # float-typed, but one branch is an int (CPython would keep the int)


@dataclass
class Call(Expr):
    func: str
    args: list[Expr]


@dataclass
class CallMethod(Expr):
    recv: Expr
    cls: ClassInfo  # static receiver class
    method: MethodInfo
    args: list[Expr]


@dataclass
class New(Expr):
    cls: ClassInfo
    args: list[Expr]


@dataclass
class StructNew(Expr):
    """Allocate an instance with every field value supplied (WasmGC struct.new)."""

    cls: ClassInfo
    values: list[Expr]


@dataclass
class GetField(Expr):
    obj: Expr
    cls: ClassInfo
    field: FieldInfo


@dataclass
class ListLit(Expr):
    items: list[Expr]


@dataclass
class ListGet(Expr):
    lst: Expr
    index: Expr


@dataclass
class ListLen(Expr):
    lst: Expr


@dataclass
class ListPop(Expr):
    lst: Expr


@dataclass
class ListAppend(Expr):
    lst: Expr
    value: Expr


@dataclass
class StrLen(Expr):
    s: Expr


@dataclass
class StrConcat(Expr):
    left: Expr
    right: Expr


@dataclass
class ToStr(Expr):
    operand: Expr


@dataclass
class TypeName(Expr):
    """type(x).__name__: read from the vtable, so it reflects the dynamic class."""

    operand: Expr
    cls: ClassInfo  # static class of operand


@dataclass
class Seq(Expr):
    """Run stmts, then evaluate value (used to keep CPython's argument evaluation order)."""

    stmts: list[Stmt]
    value: Expr


# ---- statements -------------------------------------------------------------


@dataclass
class Stmt:
    pass


@dataclass
class SetLocal(Stmt):
    name: str
    value: Expr


@dataclass
class SetField(Stmt):
    obj: Expr
    cls: ClassInfo
    field: FieldInfo
    value: Expr


@dataclass
class SetIndex(Stmt):
    lst: Expr
    index: Expr
    value: Expr


@dataclass
class ExprStmt(Stmt):
    expr: Expr


@dataclass
class Return(Stmt):
    value: Expr | None


@dataclass
class If(Stmt):
    cond: Expr
    then: list[Stmt]
    else_: list[Stmt]


@dataclass
class Loop(Stmt):
    """while cond: body; step   (continue jumps to step)."""

    cond: Expr | None
    body: list[Stmt]
    step: list[Stmt]


@dataclass
class InlineBlock(Stmt):
    """A base-class __init__ inlined by super().__init__(); ExitInline leaves it early."""

    body: list[Stmt]


@dataclass
class ExitInline(Stmt):
    """`return` inside an inlined base __init__: continue after the InlineBlock."""


@dataclass
class Break(Stmt):
    pass


@dataclass
class Continue(Stmt):
    pass


@dataclass
class Print(Stmt):
    args: list[Expr]  # already converted to str
    sep: str = " "
    end: str = "\n"


@dataclass
class Raise(Stmt):
    """throw. exc is None for a bare `raise`, which rethrows the exception held in reraise."""

    exc: Expr | None
    reraise: str | None = None


@dataclass
class Handler:
    classes: list[ClassInfo] | None  # None: bare `except:` (catches everything)
    var: str | None  # `as` name
    var_decl: Type | None  # declared type of that local (a supertype of every class caught)
    body: list[Stmt]


@dataclass
class Try(Stmt):
    body: list[Stmt]
    handlers: list[Handler]
    else_: list[Stmt]
    finally_: list[Stmt] | None
    exc_local: str  # local that receives the caught exception


# ---- top level --------------------------------------------------------------


@dataclass
class Func:
    name: str
    params: list[tuple[str, Type]]
    ret: Type
    locals: dict[str, Type]
    body: list[Stmt]
    sig_type: str | None = None  # explicit Wasm func type (methods stored in vtables)
    self_cast: ClassInfo | None = None  # downcast param `self.in` into local `self`
    nonnull_locals: set[str] = field(default_factory=set)
    export: str | None = None


@dataclass
class Module:
    funcs: list[Func]
    classes: list[ClassInfo]  # supertypes before subtypes
    main: str
