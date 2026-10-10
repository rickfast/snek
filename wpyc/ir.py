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
class Break(Stmt):
    pass


@dataclass
class Continue(Stmt):
    pass


@dataclass
class Print(Stmt):
    args: list[Expr]  # already converted to str


@dataclass
class Panic(Stmt):
    msg: Expr


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
