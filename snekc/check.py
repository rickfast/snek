"""Declaration pass + type checker. Elaborates Python AST into typed IR.

Two passes:
  1. declare  - collect classes (fields, vtable slots), function signatures and
                top-level code, so bodies can reference anything in the module.
                The built-in exception classes (builtins.py) are declared first.
  2. check    - type-check every body with local bidirectional inference,
                flow-sensitive narrowing and definite-assignment tracking, and
                lower it to ir.* as it goes.

Rules this enforces (see DESIGN.md for the reasoning):
  * signatures and class fields are annotated; locals are inferred
  * numbers are i32/i64/f32/f64; only lossless widening is implicit
  * `T | None` only for reference types; narrowing via `is not None`/isinstance
  * __init__ assigns every field before `self` is used (one struct.new);
    super().__init__(...) takes part in that phase and is inlined
  * exceptions are classes deriving from BaseException; no return/break/
    continue out of a finally block (PEP 765)
"""

from __future__ import annotations

import ast
import copy
import string
from dataclasses import dataclass, field
from functools import reduce

from . import ir, unicode
from .builtins import BUILTINS_SOURCE, STR_METHODS_TEMPLATE
from .errors import CompileError
from .types import (
    BOOL,
    BUF,
    F32,
    F64,
    I32,
    I64,
    NONE,
    NUMERIC,
    STR,
    WIDEN,
    ClassInfo,
    ClassT,
    FieldInfo,
    FuncSig,
    ListT,
    MethodInfo,
    OptionalT,
    Prim,
    Type,
    common_class,
    is_assignable,
    join_numeric,
    join_types,
    strip_optional,
)

INT_RANGE = {"i32": (-(2**31), 2**31 - 1), "i64": (-(2**63), 2**63 - 1)}

ALLOWED_IMPORTS = {
    "snek": {"i32", "i64", "f32", "f64", "Final", "final"},
    "typing": {"Final", "Optional", "final"},
    "__future__": {"annotations"},
}

BUILTINS = {"print", "len", "str", "range", "isinstance", "abs", "min", "max", "bool", "type",
            "i32", "i64", "f32", "f64", "int", "float", "list", "object", "super", "ord", "chr", "repr"}

# Runtime functions (prelude.wat) that the built-in str methods in builtins.py may call:
# name -> (parameter types, result type, Wasm function). Positions are byte offsets.
INTRINSICS: dict[str, tuple[list[Type], Type, str]] = {
    "__blen": ([STR], I32, "rt.blen"),  # byte length
    "__byte": ([STR, I32], I32, "rt.byte"),
    "__dec": ([STR, I32], I32, "rt.dec"),  # code point starting at a byte offset
    "__cpw": ([I32], I32, "rt.cpw"),  # UTF-8 length of a code point
    "__wat": ([STR, I32], I32, "rt.wat"),  # byte length of the code point at an offset
    "__back": ([STR, I32], I32, "rt.back"),  # start of the code point before an offset
    "__cplen": ([STR], I32, "rt.str_len"),  # len(s)
    "__cpoff": ([STR, I32], I32, "rt.cpoff"),  # code point index -> byte offset
    "__cpidx": ([STR, I32], I32, "rt.cpidx"),  # byte offset -> code point index
    "__bsub": ([STR, I32, I32], STR, "rt.bsub"),  # bytes [a, b)
    "__bfind": ([STR, STR, I32, I32], I32, "rt.bfind"),  # first match in bytes [a, b), or -1
    "__brfind": ([STR, STR, I32, I32], I32, "rt.brfind"),  # last match
    "__bmatch": ([STR, I32, STR], BOOL, "rt.bmatch"),  # match at a byte offset
    "__has": ([I32, I32], BOOL, "rt.has"),  # code point has a Unicode flag (constant mask)
    "__all_flags": ([STR, I32], BOOL, "rt.all_flags"),
    "__any_flags": ([STR, I32], BOOL, "rt.any_flags"),
    "__decimal": ([I32], I32, "rt.decimal"),  # decimal digit value, or -1
    "__repr": ([STR], STR, "rt.str_repr"),
    "__buf": ([I32], BUF, "rt.buf_new"),  # string builder with a capacity hint in bytes
    "__put": ([BUF, I32], NONE, "rt.buf_cp"),
    "__puts": ([BUF, STR], NONE, "rt.buf_str"),
    "__putsub": ([BUF, STR, I32, I32], NONE, "rt.buf_sub"),
    "__putcase": ([BUF, I32, I32], NONE, "rt.buf_case"),  # case mapping of a code point (constant kind)
    "__done": ([BUF], STR, "rt.buf_done"),
}
FLAG_MASK_ARG = {"__has": 1, "__all_flags": 1, "__any_flags": 1}
INT_MAX = 2**31 - 1

ARITH_OPS = {
    ast.Add: ("add", "+"), ast.Sub: ("sub", "-"), ast.Mult: ("mul", "*"),
    ast.Div: ("div", "/"), ast.FloorDiv: ("floordiv", "//"), ast.Mod: ("mod", "%"),
    ast.BitAnd: ("bitand", "&"), ast.BitOr: ("bitor", "|"), ast.BitXor: ("bitxor", "^"),
    ast.LShift: ("shl", "<<"), ast.RShift: ("shr", ">>"),
}
INT_ONLY_OPS = {"bitand", "bitor", "bitxor", "shl", "shr"}

CMP_OPS = {ast.Eq: "eq", ast.NotEq: "ne", ast.Lt: "lt", ast.LtE: "le", ast.Gt: "gt", ast.GtE: "ge"}

EXCEPTION_ATTRS = {"args", "with_traceback", "add_note"}  # BaseException attributes in CPython

PEP765 = ("would swallow any in-flight exception, so snek rejects it (PEP 765; CPython 3.14 warns about it)")


# ---- AST helpers ------------------------------------------------------------


def is_num_literal(node: ast.AST) -> bool:
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        return is_num_literal(node.operand)
    return isinstance(node, ast.Constant) and type(node.value) in (int, float)


def literal_value(node: ast.AST) -> int | float:
    if isinstance(node, ast.UnaryOp):
        v = literal_value(node.operand)
        return -v if isinstance(node.op, ast.USub) else v
    assert isinstance(node, ast.Constant)
    return node.value


def refers_to_self(node: ast.AST) -> bool:
    return any(isinstance(n, ast.Name) and n.id == "self" for n in ast.walk(node))


def names_assigned(stmts: list[ast.stmt]) -> set[str]:
    out: set[str] = set()
    for s in stmts:
        for n in ast.walk(s):
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
                out.add(n.id)
            elif isinstance(n, ast.ExceptHandler) and n.name:
                out.add(n.name)
    return out


def local_names(fn: ast.FunctionDef) -> set[str]:
    """Parameters and assigned names of a function (everything that is a local)."""
    out = {a.arg for a in fn.args.args} | names_assigned(fn.body)
    out.discard("self")
    return out


class _Renamer(ast.NodeTransformer):
    def __init__(self, mapping: dict[str, str]):
        self.mapping = mapping

    def visit_Name(self, node: ast.Name) -> ast.Name:
        node.id = self.mapping.get(node.id, node.id)
        return node

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> ast.ExceptHandler:
        self.generic_visit(node)
        if node.name:
            node.name = self.mapping.get(node.name, node.name)
        return node


def rename_locals(stmts: list[ast.stmt], mapping: dict[str, str]) -> list[ast.stmt]:
    renamer = _Renamer(mapping)
    return [renamer.visit(copy.deepcopy(s)) for s in stmts]


def has_direct_break(stmts: list[ast.stmt]) -> bool:
    """Does a `break` in stmts exit the loop whose body they are? Looks inside if/try/except
    handlers/finally, but not into nested loops (their breaks are their own)."""

    def visit(node: ast.AST) -> bool:
        if isinstance(node, ast.Break):
            return True
        if isinstance(node, (ast.While, ast.For, ast.FunctionDef, ast.ClassDef)):
            return False
        return any(visit(c) for c in ast.iter_child_nodes(node))

    return any(visit(s) for s in stmts)


def except_names(nodes: list[ast.AST]) -> set[str]:
    """Names bound by `except ... as name` anywhere in nodes. CPython deletes the name when
    the handler ends, so afterwards it is unbound even if it was assigned before."""
    return {n.name for node in nodes for n in ast.walk(node) if isinstance(n, ast.ExceptHandler) and n.name}


def is_main_guard(s: ast.stmt) -> bool:
    t = s.test if isinstance(s, ast.If) else None
    return (
        isinstance(t, ast.Compare)
        and isinstance(t.left, ast.Name) and t.left.id == "__name__"
        and len(t.ops) == 1 and isinstance(t.ops[0], ast.Eq)
        and isinstance(t.comparators[0], ast.Constant) and t.comparators[0].value == "__main__"
    )


def is_docstring(s: ast.stmt) -> bool:
    return isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant) and isinstance(s.value.value, str)


def is_super(node: ast.AST) -> bool:
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "super"


def super_init_call(s: ast.stmt) -> ast.Call | None:
    if (isinstance(s, ast.Expr) and isinstance(s.value, ast.Call) and isinstance(s.value.func, ast.Attribute)
            and s.value.func.attr == "__init__" and is_super(s.value.func.value)):
        return s.value
    return None


def int_tainted(e: ir.Expr) -> bool:
    """A float-typed expression whose CPython value may be an int (see check_ifexp)."""
    if isinstance(e, ir.IfExpr):
        return e.mixed_int or int_tainted(e.then) or int_tainted(e.else_)
    if isinstance(e, ir.Unary) and e.op in ("neg", "abs"):
        return int_tainted(e.operand)
    if isinstance(e, ir.Seq):
        return int_tainted(e.value)
    return False


def is_pure(e: ir.Expr) -> bool:
    return isinstance(e, (ir.Const, ir.StrLit, ir.NoneLit, ir.LocalGet))


def const_int(e: ir.Expr) -> int | None:
    """The value of a constant integer expression (literals combined with | and &), else None."""
    if isinstance(e, ir.Const) and isinstance(e.value, int) and not isinstance(e.value, bool):
        return e.value
    if isinstance(e, ir.Binary) and e.op in ("bitor", "bitand"):
        a, b = const_int(e.left), const_int(e.right)
        if a is not None and b is not None:
            return a | b if e.op == "bitor" else a & b
    return None


# ---- per-function state -------------------------------------------------------


@dataclass
class Flow:
    """Flow-sensitive facts at a program point."""

    assigned: set[str] = field(default_factory=set)
    narrowed: dict[str, Type] = field(default_factory=dict)

    def copy(self) -> Flow:
        return Flow(set(self.assigned), dict(self.narrowed))

    @staticmethod
    def merge(a: Flow, b: Flow) -> Flow:
        return Flow(a.assigned & b.assigned,
                    {k: v for k, v in a.narrowed.items() if b.narrowed.get(k) == v})


class FuncCtx:
    def __init__(self, name: str, ret: Type, cls: ClassInfo | None = None, is_init: bool = False,
                 owner: ClassInfo | None = None):
        self.name = name
        self.ret = ret
        self.cls = cls
        self.owner = owner  # class whose body the code belongs to; super() starts at its base
        self.is_init = is_init
        self.constructed = not is_init
        self.params: list[tuple[str, Type]] = []
        self.locals: dict[str, Type] = {}
        self.types: dict[str, Type] = {}
        self.flow = Flow()
        self.loop_depth = 0
        self.ntemps = 0
        self.handler_excs: list[str | None] = []  # caught-exception locals, innermost last; None = finally
        self.finally_loops: list[int] = []  # loop depth at entry to each enclosing finally block
        self.inline_depth = 0  # > 0 while checking a base __init__ inlined by super().__init__()
        self.inline_exits: list[bool] = []  # per inlined __init__: does a `return` leave it early?
        self.ctor_state: CtorState | None = None
        self.inline_return_ok = False  # may a `return` here leave the inlined base __init__?
        self.ninline = 0

    def add_param(self, name: str, ty: Type) -> None:
        self.params.append((name, ty))
        self.types[name] = ty
        self.flow.assigned.add(name)

    def declare(self, name: str, ty: Type) -> None:
        self.locals[name] = ty
        self.types[name] = ty

    def temp(self, ty: Type) -> str:
        name = f"t.{self.ntemps}"
        self.ntemps += 1
        self.declare(name, ty)
        self.flow.assigned.add(name)
        return name

    def get(self, name: str) -> ir.LocalGet:
        declared = self.types[name]
        return ir.LocalGet(self.flow.narrowed.get(name, declared), name, declared)


@dataclass
class CtorState:
    cls: ClassInfo  # the class being constructed
    assigned: dict[str, str] = field(default_factory=dict)  # field -> local holding its value
    out: list[ir.Stmt] = field(default_factory=list)


# ---- the checker ---------------------------------------------------------------


class Checker:
    def __init__(self, tree: ast.Module):
        self.tree = tree
        self.builtin_tree = ast.parse(BUILTINS_SOURCE, filename="<snek builtins>")
        self.classes: dict[str, ClassInfo] = {}
        self.class_order: list[ClassInfo] = []
        self.funcs: dict[str, tuple[FuncSig, ast.FunctionDef]] = {}
        self.main_stmts: list[ast.stmt] = []
        self.imported: set[str] = set()
        self.out: list[ir.Func] = []
        self.ctor_sigs: dict[ClassInfo, FuncSig] = {}
        self.ctx: FuncCtx = FuncCtx("<module>", NONE)
        # Built-in functions (the str methods): checked and emitted only when used.
        methods_src = string.Template(STR_METHODS_TEMPLATE).substitute(unicode.FLAG_NAMES)
        self.bnodes: dict[str, ast.FunctionDef] = {
            n.name: n for n in ast.parse(methods_src, filename="<snek str methods>").body
            if isinstance(n, ast.FunctionDef)}
        self.bsigs: dict[str, FuncSig] = {}
        self.bqueue: list[str] = []
        self.builtin_mode = False  # checking builtins.py code: intrinsics and __Buf are visible
        self.unicode = unicode.UnicodeUse()

    @property
    def exc_base(self) -> ClassInfo:
        return self.classes["BaseException"]

    def is_exception(self, t: Type) -> bool:
        return isinstance(t, ClassT) and t.info.is_subclass_of(self.exc_base)

    # ==== pass 1: declarations ====================================================

    def run(self) -> ir.Module:
        self.declare_module()
        for cls in self.class_order:
            self.out.append(self.check_ctor(cls))
            for m in cls.methods.values():
                if m.owner is cls:
                    self.out.append(self.check_method(cls, m))
        for name, (sig, node) in self.funcs.items():
            self.out.append(self.check_function(name, sig, node))
        self.out.append(self.check_main())
        self.builtin_mode = True
        while self.bqueue:  # built-in functions used so far, and the ones they use
            name = self.bqueue.pop()
            self.out.append(self.check_function(name, self.bsigs[name], self.bnodes[name]))
        self.builtin_mode = False
        return ir.Module(self.out, self.class_order, "snek.main", self.unicode)

    def builtin_func(self, name: str) -> FuncSig:
        """Signature of a built-in function, queueing its body to be checked and emitted."""
        if name not in self.bsigs:
            saved, self.builtin_mode = self.builtin_mode, True
            self.bsigs[name] = self.signature(self.bnodes[name], is_method=False)
            self.builtin_mode = saved
            self.bqueue.append(name)
        return self.bsigs[name]

    def declare_module(self) -> None:
        for s in self.builtin_tree.body:
            assert isinstance(s, ast.ClassDef)
            self.classes[s.name] = ClassInfo(s.name, s, builtin=True)
        pending: list[ast.FunctionDef] = []
        for s in self.tree.body:
            if isinstance(s, ast.ImportFrom):
                allowed = ALLOWED_IMPORTS.get(s.module or "")
                if allowed is None:
                    raise CompileError(f"import from '{s.module}' is not supported "
                                       f"(only snek, typing and __future__)", s)
                for a in s.names:
                    if a.name not in allowed or a.asname:
                        raise CompileError(f"cannot import '{a.name}' from {s.module}", s)
                    self.imported.add(a.name)
            elif isinstance(s, ast.Import):
                raise CompileError("plain `import` is not supported; use `from snek import i32, ...`", s)
            elif isinstance(s, ast.ClassDef):
                self.check_new_name(s.name, s)
                self.classes[s.name] = ClassInfo(s.name, s)
            elif isinstance(s, ast.FunctionDef):
                self.check_new_name(s.name, s)
                pending.append(s)
                self.funcs[s.name] = None  # type: ignore[assignment]  (filled below)
            elif isinstance(s, ast.AsyncFunctionDef):
                raise CompileError("async functions are not supported yet (planned: state machines "
                                   "on the WASI 0.3 async ABI)", s)
            elif is_main_guard(s):
                if s.orelse:
                    raise CompileError("else branch on the __main__ guard is not supported", s)
                self.main_stmts.extend(s.body)
            elif is_docstring(s):
                continue
            elif isinstance(s, (ast.Expr, ast.If, ast.While, ast.For, ast.Try, ast.Raise, ast.Assert)):
                self.main_stmts.append(s)
            elif isinstance(s, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                raise CompileError("module-level variables are not supported yet; "
                                   "keep state in locals or objects", s)
            else:
                raise CompileError(f"unsupported top-level statement: {type(s).__name__}", s)

        self.resolve_class_hierarchy()
        for cls in self.class_order:
            self.declare_members(cls)
        for node in pending:
            self.funcs[node.name] = (self.signature(node, is_method=False), node)

    def check_new_name(self, name: str, node: ast.AST) -> None:
        existing = self.classes.get(name)
        if existing is not None and existing.builtin:
            raise CompileError(f"'{name}' is a built-in exception class and cannot be redefined", node)
        if name in self.classes or name in self.funcs:
            raise CompileError(f"'{name}' is already defined", node)
        if name in BUILTINS or name in ("str", "u8s", "rt", "lit", "snek") or name in self.bnodes:
            raise CompileError(f"'{name}' is a reserved name", node)
        if not name.isascii():
            raise CompileError("identifiers must be ASCII", node)

    def resolve_class_hierarchy(self) -> None:
        for cls in self.classes.values():
            node = cls.node
            if node.keywords:
                raise CompileError("class keywords (metaclass=...) are not supported", node)
            for d in node.decorator_list:
                if not (isinstance(d, ast.Name) and d.id == "final"):
                    raise CompileError("only the @final decorator is supported on classes", d)
            if len(node.bases) > 1:
                raise CompileError("multiple inheritance is not supported (WasmGC structs have a single "
                                   "supertype); use one base class", node.bases[1])
            if node.bases:
                b = node.bases[0]
                if isinstance(b, ast.Name) and b.id == "object":
                    continue
                if not (isinstance(b, ast.Name) and b.id in self.classes):
                    raise CompileError("base class must be a class defined in this module or a built-in "
                                       "exception class", b)
                cls.base = self.classes[b.id]
                if any(isinstance(d, ast.Name) and d.id == "final" for d in cls.base.node.decorator_list):
                    raise CompileError(f"cannot subclass @final class '{cls.base.name}'", b)
                cls.base.subclasses.append(cls)

        state: dict[str, int] = {}

        def visit(c: ClassInfo) -> None:
            if state.get(c.name) == 2:
                return
            if state.get(c.name) == 1:
                raise CompileError(f"inheritance cycle involving '{c.name}'", c.node)
            state[c.name] = 1
            if c.base:
                visit(c.base)
            state[c.name] = 2
            self.class_order.append(c)

        for c in self.classes.values():
            visit(c)

    def declare_members(self, cls: ClassInfo) -> None:
        if cls.base:
            cls.fields = dict(cls.base.fields)
            cls.methods = dict(cls.base.methods)
        own_methods: set[str] = set()
        for item in cls.node.body:
            if is_docstring(item) or isinstance(item, ast.Pass):
                continue
            if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                self.declare_field(cls, item)
            elif isinstance(item, ast.FunctionDef):
                if item.decorator_list:
                    raise CompileError("method decorators (@staticmethod, @property, ...) are not "
                                       "supported yet", item.decorator_list[0])
                name = item.name
                if name in own_methods:
                    raise CompileError(f"method '{name}' is defined twice", item)
                own_methods.add(name)
                if name == "__init__":
                    cls.init_node = item
                    continue
                if name.startswith("__") and name.endswith("__"):
                    raise CompileError(f"special method {name} is not supported yet", item)
                if name in cls.fields:
                    raise CompileError(f"'{name}' is already a field of {cls.name}", item)
                self.check_exception_attr(cls, name, item)
                sig = self.signature(item, is_method=True)
                inherited = cls.methods.get(name)
                if inherited:
                    if not sig.same_as(inherited.sig):
                        raise CompileError(
                            f"{cls.name}.{name} overrides {inherited.decl.name}.{name} with a different "
                            f"signature; overrides must keep the same parameter names, types, defaults and "
                            f"return type", item)
                    cls.methods[name] = MethodInfo(name, inherited.sig, inherited.decl, inherited.slot, item, cls)
                else:
                    cls.methods[name] = MethodInfo(name, sig, cls, len(cls.methods), item, cls)
            elif isinstance(item, ast.Assign):
                raise CompileError("class attributes need a type annotation (they become instance "
                                   "fields): `name: T = value`", item)
            else:
                raise CompileError(f"unsupported statement in class body: {type(item).__name__}", item)

    def check_exception_attr(self, cls: ClassInfo, name: str, node: ast.AST) -> None:
        if name in EXCEPTION_ATTRS and cls.is_subclass_of(self.classes["BaseException"]):
            raise CompileError(f"'{name}' is reserved on exception classes (it is a BaseException attribute "
                               f"in CPython, and snek does not model it)", node)

    def declare_field(self, cls: ClassInfo, item: ast.AnnAssign) -> None:
        assert isinstance(item.target, ast.Name)
        name = item.target.id
        ann = item.annotation
        final = False
        if isinstance(ann, ast.Subscript) and isinstance(ann.value, ast.Name) and ann.value.id == "Final":
            final, ann = True, ann.slice
        elif isinstance(ann, ast.Name) and ann.id == "Final":
            raise CompileError("Final fields need an explicit type: Final[T]", ann)
        ty = self.resolve_type(ann)
        if ty == NONE:
            raise CompileError("a field cannot have type None", ann)
        if name.startswith("__") and not cls.builtin:
            raise CompileError("field names starting with '__' are reserved", item)
        self.check_exception_attr(cls, name, item)
        if name in cls.fields:
            owner = cls.fields[name].owner.name
            raise CompileError(f"field '{name}' is already declared in {owner}", item)
        if item.value is not None and not (isinstance(item.value, ast.Constant) or is_num_literal(item.value)):
            raise CompileError("field defaults must be literals", item.value)
        cls.fields[name] = FieldInfo(name, ty, final, item.value, cls, len(cls.fields) + 1)

    def signature(self, node: ast.FunctionDef, is_method: bool) -> FuncSig:
        a = node.args
        if a.vararg or a.kwarg or a.kwonlyargs or (a.posonlyargs and not self.builtin_mode):
            raise CompileError("*args, **kwargs, keyword-only and positional-only parameters are not supported yet",
                               node)
        if node.decorator_list and not is_method:
            raise CompileError("function decorators are not supported yet", node.decorator_list[0])
        args = list(a.posonlyargs) + list(a.args)
        defaults: list[ast.expr | None] = [None] * (len(args) - len(a.defaults)) + list(a.defaults)
        if is_method:
            if not args or args[0].arg != "self":
                raise CompileError("methods must take `self` as their first parameter", node)
            if defaults[0] is not None:
                raise CompileError("`self` cannot have a default value", defaults[0])
            args, defaults = args[1:], defaults[1:]
        params: list[tuple[str, Type]] = []
        for arg, d in zip(args, defaults):
            if arg.annotation is None:
                raise CompileError(f"parameter '{arg.arg}' needs a type annotation", arg)
            t = self.resolve_type(arg.annotation)
            if t == NONE:
                raise CompileError("a parameter cannot have type None", arg.annotation)
            if not arg.arg.isascii():
                raise CompileError("identifiers must be ASCII", arg)
            if d is not None:
                if not (isinstance(d, ast.Constant) or is_num_literal(d)):
                    raise CompileError("default values must be literals (numbers, strings, bools or None); "
                                       "CPython evaluates defaults once, so anything mutable would be shared", d)
                self.coerce(self.check_expr(d, t), t, d, f"default for '{arg.arg}'")
            params.append((arg.arg, t))
        if node.returns is None:
            if node.name == "__init__":
                ret: Type = NONE
            else:
                raise CompileError(f"'{node.name}' needs a return annotation (`-> T` or `-> None`)", node)
        else:
            ret = self.resolve_type(node.returns)
        return FuncSig(params, ret, defaults, len(a.posonlyargs))

    def resolve_type(self, node: ast.expr) -> Type:
        if isinstance(node, ast.Constant):
            if node.value is None:
                return NONE
            if isinstance(node.value, str):  # forward reference: "Node"
                inner = ast.parse(node.value, mode="eval").body
                ast.copy_location(inner, node)
                for n in ast.walk(inner):
                    ast.copy_location(n, node)
                return self.resolve_type(inner)
        if isinstance(node, ast.Name):
            n = node.id
            if n in NUMERIC:
                if n not in self.imported and not self.builtin_mode:
                    raise CompileError(f"'{n}' must be imported: `from snek import {n}`", node)
                return NUMERIC[n]
            if n == "__Buf" and self.builtin_mode:
                return BUF
            if n == "bool":
                return BOOL
            if n == "str":
                return STR
            if n == "int":
                raise CompileError("use a fixed-width integer type (i32 or i64) instead of int", node)
            if n == "float":
                raise CompileError("use f64 (or f32) instead of float", node)
            if n == "list":
                raise CompileError("list needs an element type: list[T]", node)
            if n in self.classes:
                return ClassT(self.classes[n])
            raise CompileError(f"unknown type '{n}'", node)
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name):
            n = node.value.id
            if n == "list":
                elem = self.resolve_type(node.slice)
                if elem == NONE:
                    raise CompileError("list[None] is not a useful type", node)
                return ListT(elem)
            if n == "Optional":
                return self.make_optional(self.resolve_type(node.slice), node)
            if n == "Final":
                raise CompileError("Final is only allowed on class fields", node)
            if n in ("dict", "set", "tuple"):
                raise CompileError(f"{n} is not supported yet", node)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            left, right = self.resolve_type(node.left), self.resolve_type(node.right)
            if right == NONE:
                return self.make_optional(left, node)
            if left == NONE:
                return self.make_optional(right, node)
            raise CompileError("only `T | None` unions are supported", node)
        raise CompileError("unsupported type annotation", node)

    def make_optional(self, t: Type, node: ast.AST) -> Type:
        if isinstance(t, OptionalT):
            return t
        if not t.is_ref:
            raise CompileError(f"'{t} | None' is not allowed: only reference types (str, list, classes) can "
                               f"be None, because numbers are never boxed", node)
        return OptionalT(t)

    def ctor_sig(self, cls: ClassInfo) -> FuncSig:
        if cls not in self.ctor_sigs:
            init = cls.effective_init()
            self.ctor_sigs[cls] = self.signature(init[0], is_method=True) if init else FuncSig([], NONE)
        return self.ctor_sigs[cls]

    # ==== pass 2: bodies ============================================================

    def check_function(self, name: str, sig: FuncSig, node: ast.FunctionDef) -> ir.Func:
        self.ctx = FuncCtx(name, sig.ret)
        for p, t in sig.params:
            self.ctx.add_param(p, t)
        body = self.check_body(node.body, sig.ret, node)
        return ir.Func(name, self.ctx.params, sig.ret, self.ctx.locals, body)

    def check_method(self, cls: ClassInfo, m: MethodInfo) -> ir.Func:
        self.ctx = FuncCtx(f"{cls.name}.{m.name}", m.sig.ret, cls, owner=cls)
        nonnull: set[str] = set()
        self_cast = None
        if m.decl is cls:
            self.ctx.add_param("self", ClassT(cls))
        else:
            # The vtable slot's type takes the introducing class; downcast once on entry.
            self.ctx.add_param("self.in", ClassT(m.decl))
            self.ctx.declare("self", ClassT(cls))
            self.ctx.flow.assigned.add("self")
            nonnull.add("self")
            self_cast = cls
        for p, t in m.sig.params:
            self.ctx.add_param(p, t)
        body = self.check_body(m.node.body, m.sig.ret, m.node)
        return ir.Func(f"{cls.name}.{m.name}", self.ctx.params, m.sig.ret, self.ctx.locals, body,
                       sig_type=f"{m.decl.name}.{m.name}.sig", self_cast=self_cast, nonnull_locals=nonnull)

    def check_main(self) -> ir.Func:
        self.ctx = FuncCtx("__main__", NONE)
        body, _ = self.check_block(self.main_stmts)
        return ir.Func("snek.main", [], NONE, self.ctx.locals, body)

    def check_body(self, stmts: list[ast.stmt], ret: Type, node: ast.AST) -> list[ir.Stmt]:
        body, terminated = self.check_block(stmts)
        if ret != NONE and not terminated:
            raise CompileError(f"'{self.ctx.name}' can reach the end without returning a {ret}", node)
        return body

    # ---- constructors -------------------------------------------------------------

    def check_ctor(self, cls: ClassInfo) -> ir.Func:
        """Lower __init__ into `C.new(params) -> C`, Swift-style two-phase init.

        WasmGC allocates a struct with all field values at once (struct.new), and
        non-null reference fields have no default. So the leading statements of
        __init__ may assign `self.f = <expr not using self>` (evaluated into
        temporaries), call super().__init__(...) (whose body is inlined and takes
        part in the same phase) and run code that does not touch self. The first
        statement that uses self triggers the allocation, by which point every
        field must have a value. The rest of __init__ then runs on the real object.
        """
        sig = self.ctor_sig(cls)
        init = cls.effective_init()
        self.ctx = ctx = FuncCtx(f"{cls.name}.__init__", NONE, cls, is_init=True)
        for p, t in sig.params:
            ctx.add_param(p, t)
        st = CtorState(cls)
        ctx.ctor_state = st
        terminated = False
        if init is not None:
            node, owner = init
            if owner is cls:
                self.require_exception_super(cls, node)
            terminated = self.init_body(st, node.body, owner)
        if not ctx.constructed and not terminated:
            st.out.append(self.construct(cls, st.assigned, init[0] if init else cls.node))
        if not terminated:
            st.out.append(ir.Return(ctx.get("self")))
        return ir.Func(f"{cls.name}.__new__", ctx.params, ClassT(cls), ctx.locals, st.out)

    def require_exception_super(self, cls: ClassInfo, node: ast.FunctionDef) -> None:
        if cls is self.exc_base or not cls.is_subclass_of(self.exc_base):
            return
        if not any(super_init_call(s) for s in node.body):
            raise CompileError(
                f"{cls.name}.__init__ must call super().__init__(message): CPython derives str(e) from the "
                f"constructor arguments when it is skipped, which snek does not emulate", node)

    def init_body(self, st: CtorState, stmts: list[ast.stmt], owner: ClassInfo) -> bool:
        ctx = self.ctx
        saved_owner, ctx.owner = ctx.owner, owner
        terminated = False
        for i, s in enumerate(stmts):
            call = super_init_call(s)
            if call is not None:
                if ctx.constructed:
                    raise CompileError("super().__init__() must come before `self` is used: the object is "
                                       "allocated once every field (including inherited ones) has a value", s)
                terminated = self.inline_super_init(st, call, owner) or terminated
                continue
            if not ctx.constructed:
                fa = self.self_field_assign(s)
                if fa is not None and not refers_to_self(fa[1]):
                    self.prefix_field_assign(st, fa[0], fa[1], owner, s)
                    continue
                if is_docstring(s):
                    continue
                if not refers_to_self(s):
                    # An early return from an inlined base __init__ is sound only if nothing after
                    # this statement allocates the object or assigns a field.
                    ctx.inline_return_ok = ctx.inline_depth > 0 and not any(
                        refers_to_self(t) or super_init_call(t) for t in stmts[i + 1:])
                    stmts_ir, term = self.check_stmt(s)
                    ctx.inline_return_ok = False
                    st.out += stmts_ir
                    terminated = terminated or term
                    continue
                if any(super_init_call(later) for later in stmts[i + 1:]):
                    raise CompileError("super().__init__() must come before `self` is used: the object is "
                                       "allocated once every field (including inherited ones) has a value, "
                                       "so move the super().__init__(...) call above this line", s)
                st.out.append(self.construct(st.cls, st.assigned, s))
            stmts_ir, term = self.check_stmt(s)
            st.out += stmts_ir
            terminated = terminated or term
        ctx.owner = saved_owner
        return terminated

    def prefix_field_assign(self, st: CtorState, fname: str, vnode: ast.expr, owner: ClassInfo,
                            s: ast.stmt) -> None:
        ctx = self.ctx
        f = st.cls.fields.get(fname)
        if f is None or (fname.startswith("__") and not owner.builtin):
            raise CompileError(f"{st.cls.name} has no field '{fname}'; declare it in the class body: "
                               f"`{fname}: T`", s)
        tmp = st.assigned.get(fname) or f"init.{fname}"
        if fname not in st.assigned:
            ctx.declare(tmp, f.ty)
            st.assigned[fname] = tmp
        st.out.append(ir.SetLocal(tmp, self.coerce(self.check_expr(vnode, f.ty), f.ty, vnode)))
        ctx.flow.assigned.add(tmp)

    def inline_super_init(self, st: CtorState, call: ast.Call, owner: ClassInfo) -> bool:
        """super().__init__(args): bind the arguments, then check the base __init__ body in place
        (locals renamed apart) as part of the same construction phase."""
        ctx = self.ctx
        sup = call.func.value  # type: ignore[attr-defined]
        if sup.args or sup.keywords:
            raise CompileError("super() with arguments is not supported; write super()", sup)
        base = owner.base
        init = base.effective_init() if base else None
        if init is None:
            if call.args or call.keywords:
                raise CompileError("object.__init__() takes no arguments", call)
            return False
        node, init_owner = init
        sig = self.signature(node, is_method=True)
        prelude, args = self.bind_args(sig, call, f"{base.name}.__init__")  # type: ignore[union-attr]
        st.out += prelude
        ctx.ninline += 1
        mapping = {n: f"{init_owner.name}.{ctx.ninline}.{n}" for n in local_names(node)}
        for (p, t), a in zip(sig.params, args):
            ctx.declare(mapping[p], t)
            st.out.append(ir.SetLocal(mapping[p], a))
            ctx.flow.assigned.add(mapping[p])
        outer, st.out = st.out, []
        ctx.inline_depth += 1
        ctx.inline_exits.append(False)
        terminated = self.init_body(st, rename_locals(node.body, mapping), init_owner)
        exited = ctx.inline_exits.pop()
        ctx.inline_depth -= 1
        outer.append(ir.InlineBlock(st.out))  # a `return` in the base __init__ leaves just this block
        st.out = outer
        return terminated and not exited

    def self_field_assign(self, s: ast.stmt) -> tuple[str, ast.expr] | None:
        target = value = None
        if isinstance(s, ast.Assign) and len(s.targets) == 1:
            target, value = s.targets[0], s.value
        elif isinstance(s, ast.AnnAssign) and s.value is not None:
            target, value = s.target, s.value
        if (isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name)
                and target.value.id == "self" and value is not None):
            return target.attr, value
        return None

    def field_values(self, cls: ClassInfo, assigned: dict[str, str], at: ast.AST, when: str) -> list[ir.Expr]:
        values: list[ir.Expr] = []
        missing: list[str] = []
        for f in cls.fields.values():
            if f.name in assigned:
                values.append(self.ctx.get(assigned[f.name]))
            elif f.default is not None:
                values.append(self.coerce(self.check_expr(f.default, f.ty), f.ty, f.default))
            else:
                missing.append(f.name)
        if missing:
            names = ", ".join(f"self.{m}" for m in missing)
            raise CompileError(
                f"{cls.name}.__init__ must assign {names} {when} (or the field needs a default in the class "
                f"body). Objects are allocated in one step with every field set, so field assignments have "
                f"to come first.", at)
        return values

    def construct(self, cls: ClassInfo, assigned: dict[str, str], at: ast.AST) -> ir.Stmt:
        values = self.field_values(cls, assigned, at, "before `self` is used")
        self.ctx.declare("self", ClassT(cls))
        self.ctx.flow.assigned.add("self")
        self.ctx.constructed = True
        return ir.SetLocal("self", ir.StructNew(ClassT(cls), cls, values))

    # ---- statements -------------------------------------------------------------

    def check_block(self, stmts: list[ast.stmt]) -> tuple[list[ir.Stmt], bool]:
        out: list[ir.Stmt] = []
        terminated = False
        for s in stmts:
            stmts_ir, term = self.check_stmt(s)
            out += stmts_ir
            terminated = terminated or term
        return out, terminated

    def check_stmt(self, s: ast.stmt) -> tuple[list[ir.Stmt], bool]:
        ctx = self.ctx
        if isinstance(s, ast.Expr):
            if is_docstring(s):
                return [], False
            v = s.value
            if isinstance(v, ast.Call) and isinstance(v.func, ast.Name) and v.func.id == "print":
                return [self.check_print(v)], False
            return [ir.ExprStmt(self.check_expr(v))], False
        if isinstance(s, ast.Assign):
            if len(s.targets) != 1:
                raise CompileError("chained assignment (a = b = ...) is not supported", s)
            return self.check_assign(s.targets[0], s.value, None, s), False
        if isinstance(s, ast.AnnAssign):
            if not isinstance(s.target, ast.Name):
                if s.value is None:
                    raise CompileError("annotation without a value is only allowed on local names", s)
                return self.check_assign(s.target, s.value, None, s), False
            ty = self.resolve_type(s.annotation)
            if ty == NONE:
                raise CompileError("a variable cannot have type None", s.annotation)
            name = s.target.id
            if name in ctx.types and ctx.types[name] != ty:
                raise CompileError(f"'{name}' was already declared as {ctx.types[name]}", s)
            if name not in ctx.types:
                ctx.declare(name, ty)
            if s.value is None:
                return [], False
            return self.check_assign(s.target, s.value, ty, s), False
        if isinstance(s, ast.AugAssign):
            return self.check_augassign(s), False
        if isinstance(s, ast.Return):
            return self.check_return(s), True
        if isinstance(s, ast.If):
            return self.check_if(s)
        if isinstance(s, ast.While):
            return self.check_while(s)
        if isinstance(s, ast.For):
            return self.check_for(s), False
        if isinstance(s, (ast.Break, ast.Continue)):
            word = type(s).__name__.lower()
            if ctx.loop_depth == 0:
                raise CompileError(f"'{word}' outside a loop", s)
            if ctx.finally_loops and ctx.loop_depth == ctx.finally_loops[-1]:
                raise CompileError(f"'{word}' out of a finally block {PEP765}", s)
            return [ir.Break() if isinstance(s, ast.Break) else ir.Continue()], True
        if isinstance(s, ast.Pass):
            return [], False
        if isinstance(s, ast.Assert):
            return self.check_assert(s), False
        if isinstance(s, ast.Raise):
            return [self.check_raise(s)], True
        if isinstance(s, ast.Try):
            return self.check_try(s)
        if isinstance(s, ast.TryStar):
            raise CompileError("except* (exception groups) is not supported", s)
        if isinstance(s, (ast.With, ast.AsyncWith)):
            raise CompileError("with statements are not supported yet (they need __enter__/__exit__)", s)
        if isinstance(s, (ast.FunctionDef, ast.ClassDef, ast.Lambda)):
            raise CompileError("nested functions and classes are not supported yet", s)
        if isinstance(s, (ast.Global, ast.Nonlocal)):
            raise CompileError("global/nonlocal are not supported", s)
        raise CompileError(f"unsupported statement: {type(s).__name__}", s)

    def check_print(self, call: ast.Call) -> ir.Stmt:
        opts = {"sep": " ", "end": "\n"}
        for kw in call.keywords:
            if kw.arg not in opts:
                raise CompileError(f"print() keyword '{kw.arg}' is not supported (sep= and end= are)", kw)
            if not (isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str)):
                raise CompileError(f"print({kw.arg}=...) must be a string literal", kw.value)
            opts[kw.arg] = kw.value.value
        if any(isinstance(a, ast.Starred) for a in call.args):
            raise CompileError("print(*args) is not supported", call)
        return ir.Print([self.to_str(self.check_expr(a), a) for a in call.args], opts["sep"], opts["end"])

    def check_assign(self, target: ast.expr, value: ast.expr, ann: Type | None, node: ast.AST) -> list[ir.Stmt]:
        ctx = self.ctx
        if isinstance(target, ast.Name):
            name = target.id
            if name == "self" and ctx.cls is not None:
                raise CompileError("cannot assign to self", target)
            if not name.isascii():
                raise CompileError("identifiers must be ASCII", target)
            declared = ctx.types.get(name)
            if declared is not None:
                v = self.coerce(self.check_expr(value, declared), declared, value)
            else:
                v = self.check_expr(value)
                self.no_int_taint(v, value)
                if v.ty == NONE:
                    raise CompileError(f"cannot infer a type for '{name}' from None; annotate it, "
                                       f"e.g. `{name}: Node | None = None`", node)
                if isinstance(v, ir.ListLit) and not v.items and ann is None:
                    raise CompileError("cannot infer the element type of []", value)
                ctx.declare(name, v.ty)
                declared = v.ty
            ctx.flow.assigned.add(name)
            # Assigning a more precise value narrows the local (e.g. Node into Node | None).
            if v.ty != declared and v.ty != NONE and is_assignable(v.ty, declared):
                ctx.flow.narrowed[name] = v.ty
            else:
                ctx.flow.narrowed.pop(name, None)
            return [ir.SetLocal(name, v)]
        # CPython evaluates the right-hand side before the target object and index.
        if isinstance(target, ast.Attribute):
            obj = self.check_expr(target.value)
            cls, f = self.field_of(obj, target)
            if f.final:
                raise CompileError(f"'{f.name}' is Final and can only be assigned in __init__ before `self` "
                                   f"is used", target)
            pre, v = self.value_first(self.coerce(self.check_expr(value, f.ty), f.ty, value), f.ty, [obj])
            return pre + [ir.SetField(obj, cls, f, v)]
        if isinstance(target, ast.Subscript):
            if isinstance(target.slice, ast.Slice):
                raise CompileError("slice assignment is not supported yet", target)
            lst = self.check_expr(target.value)
            if lst.ty == STR:
                raise CompileError("str does not support item assignment (strings are immutable)", target)
            elem = self.list_elem(lst, target.value)
            idx = self.check_index(target.slice)
            pre, v = self.value_first(self.coerce(self.check_expr(value, elem), elem, value), elem, [lst, idx])
            return pre + [ir.SetIndex(lst, idx, v)]
        if isinstance(target, (ast.Tuple, ast.List)):
            raise CompileError("tuple unpacking is not supported yet", target)
        raise CompileError("unsupported assignment target", target)

    def value_first(self, v: ir.Expr, ty: Type, targets: list[ir.Expr]) -> tuple[list[ir.Stmt], ir.Expr]:
        if is_pure(v) or all(is_pure(t) for t in targets):
            return [], v
        tmp = self.ctx.temp(ty)
        return [ir.SetLocal(tmp, v)], self.ctx.get(tmp)

    def check_augassign(self, s: ast.AugAssign) -> list[ir.Stmt]:
        ctx = self.ctx
        op = ARITH_OPS.get(type(s.op))
        if op is None:
            raise CompileError("unsupported augmented assignment", s)
        t = s.target
        if isinstance(t, ast.Name):
            if t.id not in ctx.types:
                raise CompileError(f"undefined name '{t.id}'", t)
            cur = self.load_name(t)
            if isinstance(cur.ty, ListT):
                return [ir.ExprStmt(self.list_inplace(s, cur))]
            new = self.arith(s.op, cur, self.check_operand(s.value, cur.ty), s)
            declared = ctx.types[t.id]
            ctx.flow.narrowed.pop(t.id, None)
            return [ir.SetLocal(t.id, self.coerce(new, declared, s))]
        if isinstance(t, ast.Attribute):
            obj = self.check_expr(t.value)
            cls, f = self.field_of(obj, t)
            if f.final:
                raise CompileError(f"'{f.name}' is Final", t)
            tmp = ctx.temp(obj.ty)
            cur = ir.GetField(f.ty, ctx.get(tmp), cls, f)
            if isinstance(f.ty, ListT):
                return [ir.SetLocal(tmp, obj), ir.ExprStmt(self.list_inplace(s, cur))]
            new = self.arith(s.op, cur, self.check_operand(s.value, f.ty), s)
            return [ir.SetLocal(tmp, obj), ir.SetField(ctx.get(tmp), cls, f, self.coerce(new, f.ty, s))]
        if isinstance(t, ast.Subscript):
            if isinstance(t.slice, ast.Slice):
                raise CompileError("slice assignment is not supported yet", t)
            lst = self.check_expr(t.value)
            if lst.ty == STR:
                raise CompileError("str does not support item assignment (strings are immutable)", t)
            elem = self.list_elem(lst, t.value)
            ltmp, itmp = ctx.temp(lst.ty), ctx.temp(I32)
            idx = self.check_index(t.slice)
            cur = ir.ListGet(elem, ctx.get(ltmp), ctx.get(itmp))
            if isinstance(elem, ListT):
                return [ir.SetLocal(ltmp, lst), ir.SetLocal(itmp, idx), ir.ExprStmt(self.list_inplace(s, cur))]
            new = self.arith(s.op, cur, self.check_operand(s.value, elem), s)
            return [ir.SetLocal(ltmp, lst), ir.SetLocal(itmp, idx),
                    ir.SetIndex(ctx.get(ltmp), ctx.get(itmp), self.coerce(new, elem, s))]
        raise CompileError("unsupported augmented assignment target", t)

    def list_inplace(self, s: ast.AugAssign, cur: ir.Expr) -> ir.Expr:
        """xs += ys extends xs and xs *= n repeats it, in place (other references see the change)."""
        t = cur.ty
        if isinstance(s.op, ast.Add):
            other = self.coerce(self.check_expr(s.value, t), t, s.value)
            return ir.ListOp(NONE, "extend", cur, [other])
        if isinstance(s.op, ast.Mult):
            n = self.check_expr(s.value, I32)
            if n.ty != I32:
                raise CompileError(f"a list can only be repeated by an i32, got {n.ty}", s.value)
            return ir.ListOp(NONE, "imul", cur, [n])
        raise CompileError(f"unsupported augmented assignment on {t}", s)

    def check_return(self, s: ast.Return) -> list[ir.Stmt]:
        ctx = self.ctx
        if ctx.finally_loops:
            raise CompileError(f"'return' in a finally block {PEP765}", s)
        if ctx.is_init:
            if s.value is not None:
                raise CompileError("__init__ cannot return a value", s)
            if not ctx.constructed:
                if ctx.inline_depth:
                    if not ctx.inline_return_ok:
                        raise CompileError(
                            "this return in a base __init__ (reached through super().__init__()) would skip code "
                            "that uses `self` or assigns a field before the object exists; that is only "
                            "supported when nothing after it in that __init__ does", s)
                    ctx.inline_exits[-1] = True  # nothing skipped builds the object: just leave the block
                    return [ir.ExitInline()]
                # Every field has its value already: allocate the object on this path and return it.
                st = ctx.ctor_state
                assert st is not None
                values = self.field_values(st.cls, st.assigned, s, "before returning")
                if "self" not in ctx.types:
                    ctx.declare("self", ClassT(st.cls))
                return [ir.SetLocal("self", ir.StructNew(ClassT(st.cls), st.cls, values)),
                        ir.Return(ir.LocalGet(ClassT(st.cls), "self", ClassT(st.cls)))]
            if ctx.inline_depth:  # returns from the base __init__ to the subclass __init__
                ctx.inline_exits[-1] = True
                return [ir.ExitInline()]
            return [ir.Return(ctx.get("self"))]
        if ctx.ret == NONE:
            if s.value is not None and not (isinstance(s.value, ast.Constant) and s.value.value is None):
                raise CompileError(f"'{ctx.name}' is declared `-> None` but returns a value", s)
            return [ir.Return(None)]
        if s.value is None:
            raise CompileError(f"'{ctx.name}' must return a {ctx.ret}", s)
        return [ir.Return(self.coerce(self.check_expr(s.value, ctx.ret), ctx.ret, s.value))]

    def check_assert(self, s: ast.Assert) -> list[ir.Stmt]:
        cond = self.check_cond(s.test)
        msg = None if s.msg is None else self.to_str(self.check_expr(s.msg), s.msg)
        true_facts, _ = self.facts(s.test)
        self.ctx.flow.narrowed.update(true_facts)  # a failed assert never falls through
        exc = self.new_exception("AssertionError", msg)
        return [ir.If(ir.Unary(BOOL, "not", cond), [ir.Raise(exc)], [])]

    def new_exception(self, cls_name: str, msg: ir.Expr | None) -> ir.Expr:
        cls = self.classes[cls_name]
        return ir.New(ClassT(cls), cls, [msg if msg is not None else ir.NoneLit(OptionalT(STR))])

    # ---- exceptions -------------------------------------------------------------

    def check_raise(self, s: ast.Raise) -> ir.Stmt:
        ctx = self.ctx
        if s.cause is not None:
            raise CompileError("`raise ... from ...` is not supported yet (no exception chaining)", s)
        if s.exc is None:
            if not ctx.handler_excs:
                raise CompileError("bare `raise` is only allowed inside an except block", s)
            if ctx.handler_excs[-1] is None:
                raise CompileError("bare `raise` in a finally block is not supported: it re-raises "
                                   "whichever exception is in flight, which depends on the path taken", s)
            return ir.Raise(None, ctx.handler_excs[-1])
        e = s.exc
        if isinstance(e, ast.Name) and e.id in self.classes and e.id not in ctx.types:
            # `raise ValueError` instantiates the class with no arguments
            cls = self.classes[e.id]
            if not cls.is_subclass_of(self.exc_base):
                raise CompileError(f"exceptions must derive from BaseException, and {cls.name} does not", e)
            call = ast.copy_location(ast.Call(func=e, args=[], keywords=[]), e)
            prelude, args = self.bind_args(self.ctor_sig(cls), call, cls.name)
            return ir.Raise(self.seq(prelude, ir.New(ClassT(cls), cls, args)))
        v = self.check_expr(e)
        if isinstance(v.ty, OptionalT) and self.is_exception(v.ty.inner):
            raise CompileError(f"this may be None (type {v.ty}); check `is not None` before raising it", e)
        if not self.is_exception(v.ty):
            raise CompileError(f"exceptions must derive from BaseException, got {v.ty}", e)
        return ir.Raise(v)

    def exc_classes(self, node: ast.expr) -> list[ClassInfo]:
        elts = node.elts if isinstance(node, ast.Tuple) else [node]
        out: list[ClassInfo] = []
        for e in elts:
            if not (isinstance(e, ast.Name) and e.id in self.classes):
                raise CompileError("except needs an exception class or a tuple of them", e)
            c = self.classes[e.id]
            if not c.is_subclass_of(self.exc_base):
                raise CompileError(f"'{c.name}' is not an exception class (it does not derive from "
                                   f"BaseException)", e)
            out.append(c)
        return out

    def check_try(self, s: ast.Try) -> tuple[list[ir.Stmt], bool]:
        """Flow rules: a handler can start after any prefix of the try body, so it sees only what
        was definitely assigned before `try`, minus narrowings the body may have invalidated. The
        finally block can start almost anywhere, so it gets the same treatment for the whole
        statement. After the statement, the paths that fall through are merged."""
        ctx = self.ctx
        before = ctx.flow
        exc_local = ctx.temp(OptionalT(ClassT(self.exc_base)))
        body_names = names_assigned(s.body)

        ctx.flow = before.copy()
        body_ir, body_term = self.check_block(s.body)
        else_ir, else_term = self.check_block(s.orelse)
        normal_term = body_term or else_term
        exits: list[Flow] = [] if normal_term else [ctx.flow]
        all_term = normal_term

        handlers: list[ir.Handler] = []
        for i, h in enumerate(s.handlers):
            if h.type is None and i != len(s.handlers) - 1:
                raise CompileError("a bare `except:` must be the last handler", h)
            classes = None if h.type is None else self.exc_classes(h.type)
            ctx.flow = before.copy()
            for n in body_names:
                ctx.flow.narrowed.pop(n, None)
            ctx.flow.assigned -= except_names(s.body)  # a nested handler in the body may have run
            var_decl: Type | None = None
            if h.name:
                caught = ClassT(reduce(lambda a, b: common_class(a, b) or self.exc_base,  # type: ignore[arg-type]
                                       classes or [self.exc_base]))
                var_decl = ctx.types.get(h.name)
                if var_decl is None:
                    var_decl = ClassT(self.exc_base)
                    ctx.declare(h.name, var_decl)
                elif not (isinstance(strip_optional(var_decl), ClassT) and is_assignable(caught, var_decl)):
                    raise CompileError(f"'{h.name}' is already a local of type {var_decl}; pick another name "
                                       f"for the exception", h)
                ctx.flow.assigned.add(h.name)
                if caught != var_decl:
                    ctx.flow.narrowed[h.name] = caught
                else:
                    ctx.flow.narrowed.pop(h.name, None)
            ctx.handler_excs.append(exc_local)
            h_ir, h_term = self.check_block(h.body)
            ctx.handler_excs.pop()
            ctx.flow.assigned -= except_names(h.body)
            if h.name:  # CPython deletes the name at the end of the handler
                ctx.flow.assigned.discard(h.name)
                ctx.flow.narrowed.pop(h.name, None)
            handlers.append(ir.Handler(classes, h.name, var_decl, h_ir))
            if not h_term:
                exits.append(ctx.flow)
            all_term = all_term and h_term

        merged = reduce(Flow.merge, exits) if exits else before.copy()
        fin_ir: list[ir.Stmt] | None = None
        fin_term = False
        if s.finalbody:
            ctx.flow = before.copy()
            region: list[ast.AST] = [*s.body, *s.orelse, *s.handlers]
            for n in names_assigned(region):  # type: ignore[arg-type]
                ctx.flow.narrowed.pop(n, None)
            ctx.flow.assigned -= except_names(region)
            ctx.finally_loops.append(ctx.loop_depth)
            ctx.handler_excs.append(None)  # a bare raise here would depend on the path taken
            fin_ir, fin_term = self.check_block(s.finalbody)
            ctx.handler_excs.pop()
            ctx.finally_loops.pop()
            # the finally body runs on every path: what it deletes is gone, what it assigns is there
            merged.assigned = (merged.assigned - except_names(s.finalbody)) | ctx.flow.assigned
            for n in names_assigned(s.finalbody):
                merged.narrowed.pop(n, None)
                if n in ctx.flow.narrowed:
                    merged.narrowed[n] = ctx.flow.narrowed[n]
        terminated = all_term or fin_term
        ctx.flow = before.copy() if terminated else merged
        return [ir.Try(body_ir, handlers, else_ir, fin_ir, exc_local)], terminated

    # ---- if / loops ---------------------------------------------------------------

    def check_if(self, s: ast.If) -> tuple[list[ir.Stmt], bool]:
        ctx = self.ctx
        cond = self.check_cond(s.test)
        true_facts, false_facts = self.facts(s.test)
        before = ctx.flow
        ctx.flow = before.copy()
        ctx.flow.narrowed.update(true_facts)
        then_ir, then_term = self.check_block(s.body)
        then_flow = ctx.flow
        ctx.flow = before.copy()
        ctx.flow.narrowed.update(false_facts)
        else_ir, else_term = self.check_block(s.orelse)
        else_flow = ctx.flow
        if then_term and else_term:
            ctx.flow = before.copy()
        elif then_term:
            ctx.flow = else_flow
        elif else_term:
            ctx.flow = then_flow
        else:
            ctx.flow = Flow.merge(then_flow, else_flow)
        return [ir.If(cond, then_ir, else_ir)], then_term and else_term

    def enter_loop(self, body: list[ast.stmt]) -> Flow:
        """Facts that hold at every iteration: drop narrowings the body may invalidate."""
        before = self.ctx.flow
        loop_flow = before.copy()
        for n in names_assigned(body):
            loop_flow.narrowed.pop(n, None)
        loop_flow.assigned -= except_names(body)  # deleted by a handler in an earlier iteration
        self.ctx.flow = loop_flow
        return loop_flow

    def check_loop_body(self, body: list[ast.stmt], prefix: list[ir.Stmt] | None = None) -> list[ir.Stmt]:
        self.ctx.loop_depth += 1
        stmts, _ = self.check_block(body)
        self.ctx.loop_depth -= 1
        return (prefix or []) + stmts

    def check_while(self, s: ast.While) -> tuple[list[ir.Stmt], bool]:
        if s.orelse:
            raise CompileError("while/else is not supported", s)
        ctx = self.ctx
        loop_flow = self.enter_loop(s.body)
        cond = self.check_cond(s.test)
        true_facts, _ = self.facts(s.test)
        ctx.flow = loop_flow.copy()
        ctx.flow.narrowed.update(true_facts)
        body = self.check_loop_body(s.body)
        ctx.flow = loop_flow.copy()  # body assignments are not definite after the loop
        infinite = isinstance(s.test, ast.Constant) and s.test.value is True
        return [ir.Loop(None if infinite else cond, body, [])], infinite and not has_direct_break(s.body)

    def check_for(self, s: ast.For) -> list[ir.Stmt]:
        if s.orelse:
            raise CompileError("for/else is not supported", s)
        if not isinstance(s.target, ast.Name):
            raise CompileError("for-loop target must be a simple name (no unpacking yet)", s.target)
        ctx = self.ctx
        var = s.target.id
        it = s.iter
        loop_flow = self.enter_loop(s.body + [s])
        if isinstance(it, ast.Call) and isinstance(it.func, ast.Name) and it.func.id == "range":
            stmts = self.lower_range_for(var, it, s, loop_flow)
        else:
            seq = self.check_expr(it)
            if seq.ty == STR:
                return self.lower_str_for(var, seq, s, loop_flow)
            elem = self.list_elem(seq, it)
            self.bind_loop_var(var, elem, s.target)
            ltmp, itmp = ctx.temp(seq.ty), ctx.temp(I32)
            head = [ir.SetLocal(var, ir.ListGet(elem, ctx.get(ltmp), ctx.get(itmp)))]
            ctx.flow = loop_flow.copy()  # the loop variable is only assigned inside the body
            ctx.flow.assigned |= {var, ltmp, itmp}
            body = self.check_loop_body(s.body, head)
            cond = ir.Compare(BOOL, "lt", I32, ctx.get(itmp), ir.ListLen(I32, ctx.get(ltmp)))
            step = [ir.SetLocal(itmp, ir.Binary(I32, "add", ctx.get(itmp), ir.Const(I32, 1)))]
            stmts = [ir.SetLocal(ltmp, seq), ir.SetLocal(itmp, ir.Const(I32, 0)), ir.Loop(cond, body, step)]
        ctx.flow = loop_flow.copy()
        return stmts

    def lower_str_for(self, var: str, seq: ir.Expr, s: ast.For, loop_flow: Flow) -> list[ir.Stmt]:
        """for ch in s: walk the UTF-8 bytes, yielding one-character strings."""
        ctx = self.ctx
        self.bind_loop_var(var, STR, s.target)
        stmp, itmp = ctx.temp(STR), ctx.temp(I32)
        head = [ir.SetLocal(var, ir.Call(STR, "rt.char_at", [ctx.get(stmp), ctx.get(itmp)]))]
        ctx.flow = loop_flow.copy()
        ctx.flow.assigned |= {var, stmp, itmp}
        body = self.check_loop_body(s.body, head)
        cond = ir.Compare(BOOL, "lt", I32, ctx.get(itmp), ir.Call(I32, "rt.blen", [ctx.get(stmp)]))
        step = [ir.SetLocal(itmp, ir.Binary(I32, "add", ctx.get(itmp),
                                            ir.Call(I32, "rt.wat", [ctx.get(stmp), ctx.get(itmp)])))]
        ctx.flow = loop_flow.copy()
        return [ir.SetLocal(stmp, seq), ir.SetLocal(itmp, ir.Const(I32, 0)), ir.Loop(cond, body, step)]

    def lower_range_for(self, var: str, call: ast.Call, s: ast.For, loop_flow: Flow) -> list[ir.Stmt]:
        ctx = self.ctx
        args = call.args
        if call.keywords or not 1 <= len(args) <= 3:
            raise CompileError("range() takes 1 to 3 positional arguments", call)
        step = 1
        if len(args) == 3:
            if not is_num_literal(args[2]) or not isinstance(literal_value(args[2]), int) or literal_value(args[2]) == 0:
                raise CompileError("range() step must be a non-zero integer literal", args[2])
            step = int(literal_value(args[2]))
        bounds = args[:2] if len(args) >= 2 else [ast.copy_location(ast.Constant(0), call), args[0]]
        declared = ctx.types.get(var)
        if declared is not None and not (isinstance(declared, Prim) and declared.is_int):
            raise CompileError(f"loop variable '{var}' is {declared}, but range() yields integers", s.target)
        # Literal bounds take their type from the other bound, the variable, or default to i32.
        hint = declared
        non_lit = [self.check_expr(b, hint) for b in bounds if not is_num_literal(b)]
        if hint is None:
            for e in non_lit:
                hint = join_numeric(hint, e.ty) if hint else e.ty
            hint = hint or I32
        if not (isinstance(hint, Prim) and hint.is_int):
            raise CompileError(f"range() bounds must be integers, got {hint}", call)
        it = iter(non_lit)
        start, stop = [self.coerce(next(it) if not is_num_literal(b) else self.check_expr(b, hint), hint, b)
                       for b in bounds]
        self.bind_loop_var(var, hint, s.target)
        ctr, end = ctx.temp(hint), ctx.temp(hint)
        head = [ir.SetLocal(var, ctx.get(ctr))]
        ctx.flow = loop_flow.copy()  # the loop variable is only assigned inside the body
        ctx.flow.assigned |= {var, ctr, end}
        body = self.check_loop_body(s.body, head)
        cond = ir.Compare(BOOL, "lt" if step > 0 else "gt", hint, ctx.get(ctr), ctx.get(end))
        inc = [ir.SetLocal(ctr, ir.Binary(hint, "add", ctx.get(ctr), ir.Const(hint, step)))]
        return [ir.SetLocal(ctr, start), ir.SetLocal(end, stop), ir.Loop(cond, body, inc)]

    def bind_loop_var(self, var: str, ty: Type, node: ast.AST) -> None:
        declared = self.ctx.types.get(var)
        if declared is None:
            self.ctx.declare(var, ty)
        elif declared != ty:
            raise CompileError(f"loop variable '{var}' is {declared} but the loop yields {ty}", node)
        self.ctx.flow.narrowed.pop(var, None)

    # ---- narrowing --------------------------------------------------------------

    def facts(self, node: ast.expr) -> tuple[dict[str, Type], dict[str, Type]]:
        """(facts if node is true, facts if node is false) about local names."""
        ctx = self.ctx
        if (isinstance(node, ast.Compare) and len(node.ops) == 1 and isinstance(node.ops[0], (ast.Is, ast.IsNot))
                and isinstance(node.left, ast.Name) and isinstance(node.comparators[0], ast.Constant)
                and node.comparators[0].value is None and node.left.id in ctx.types):
            cur = ctx.get(node.left.id).ty
            nn = {node.left.id: cur.inner} if isinstance(cur, OptionalT) else {}
            return (nn, {}) if isinstance(node.ops[0], ast.IsNot) else ({}, nn)
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "isinstance"
                and len(node.args) == 2 and isinstance(node.args[0], ast.Name) and node.args[0].id in ctx.types
                and isinstance(node.args[1], ast.Name) and node.args[1].id in self.classes):
            cur = strip_optional(ctx.get(node.args[0].id).ty)
            target = self.classes[node.args[1].id]
            if isinstance(cur, ClassT) and target.is_subclass_of(cur.info):
                return {node.args[0].id: ClassT(target)}, {}
            return {}, {}
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            t, f = self.facts(node.operand)
            return f, t
        if isinstance(node, ast.BoolOp):
            merged: dict[str, Type] = {}
            for v in node.values:
                t, f = self.facts(v)
                merged.update(t if isinstance(node.op, ast.And) else f)
            return (merged, {}) if isinstance(node.op, ast.And) else ({}, merged)
        return {}, {}

    # ---- expressions -------------------------------------------------------------

    def check_cond(self, node: ast.expr) -> ir.Expr:
        e = self.check_expr(node, BOOL)
        if e.ty != BOOL:
            hint = ""
            if isinstance(e.ty, OptionalT):
                hint = "; write `x is not None`"
            elif isinstance(e.ty, ListT) or e.ty == STR:
                hint = "; write `len(x) > 0`"
            elif isinstance(e.ty, Prim):
                hint = "; write `x != 0`"
            raise CompileError(f"condition must be bool, got {e.ty} (no implicit truthiness){hint}", node)
        return e

    def check_operand(self, node: ast.expr, other: Type) -> ir.Expr:
        """Check one operand of a binary op; numeric literals adopt the other side's type."""
        if is_num_literal(node) and isinstance(other, Prim) and other.is_numeric:
            return self.check_expr(node, other)
        if isinstance(node, ast.List) and not node.elts and isinstance(other, ListT):
            return self.check_expr(node, other)  # xs + []
        return self.check_expr(node)

    def check_operands(self, left: ast.expr, right: ast.expr, expected: Type | None) -> tuple[ir.Expr, ir.Expr]:
        # Only an int context flows into literal operands: in a float context, 2 + 3 is still the int 5.
        num_expected = expected if isinstance(expected, Prim) and expected.is_int else None
        empty_left = isinstance(left, ast.List) and not left.elts
        if (is_num_literal(left) and not is_num_literal(right)) or empty_left:
            r = self.check_expr(right, num_expected)
            return self.check_operand(left, r.ty), r
        l = self.check_expr(left, num_expected)
        return l, self.check_operand(right, l.ty)

    def check_expr(self, node: ast.expr, expected: Type | None = None) -> ir.Expr:
        if isinstance(node, ast.Constant):
            return self.check_constant(node, expected)
        if isinstance(node, ast.Name):
            return self.load_name(node)
        if isinstance(node, ast.UnaryOp):
            if is_num_literal(node):
                return self.number(literal_value(node), expected, node)
            if isinstance(node.op, ast.Not):
                return ir.Unary(BOOL, "not", self.check_cond(node.operand))
            int_only = isinstance(node.op, ast.Invert) and not (isinstance(expected, Prim) and expected.is_int)
            v = self.check_expr(node.operand, None if int_only else expected)
            if not (isinstance(v.ty, Prim) and v.ty.is_numeric):
                raise CompileError(f"unary operator on non-number {v.ty}", node)
            if isinstance(node.op, ast.UAdd):
                return v
            if isinstance(node.op, ast.Invert):
                if not v.ty.is_int:
                    raise CompileError("~ needs an integer", node)
                return ir.Unary(v.ty, "invert", v)
            return ir.Unary(v.ty, "neg", v)
        if isinstance(node, ast.BinOp):
            l, r = self.check_operands(node.left, node.right, expected)
            return self.arith(node.op, l, r, node)
        if isinstance(node, ast.BoolOp):
            return self.check_boolop(node)
        if isinstance(node, ast.Compare):
            return self.check_compare(node)
        if isinstance(node, ast.Call):
            return self.check_call(node, expected)
        if isinstance(node, ast.Attribute):
            return self.check_attribute(node)
        if isinstance(node, ast.Subscript):
            seq = self.check_expr(node.value)
            if isinstance(node.slice, ast.Slice):
                bounds = self.check_slice(node.slice)
                if seq.ty == STR:
                    return ir.Call(STR, "rt.str_slice", [seq, *bounds])
                self.list_elem(seq, node.value)
                return ir.ListOp(seq.ty, "slice", seq, bounds)
            if seq.ty == STR:
                return ir.Call(STR, "rt.str_get", [seq, self.check_index(node.slice)])
            elem = self.list_elem(seq, node.value)
            return ir.ListGet(elem, seq, self.check_index(node.slice))
        if isinstance(node, ast.List):
            return self.check_list(node, expected)
        if isinstance(node, ast.JoinedStr):
            return self.check_fstring(node)
        if isinstance(node, ast.IfExp):
            return self.check_ifexp(node, expected)
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            raise CompileError("comprehensions are not supported yet", node)
        if isinstance(node, (ast.Dict, ast.Set, ast.Tuple)):
            raise CompileError(f"{type(node).__name__.lower()} literals are not supported yet", node)
        if isinstance(node, ast.Lambda):
            raise CompileError("lambdas are not supported yet", node)
        if isinstance(node, ast.NamedExpr):
            raise CompileError("the walrus operator is not supported", node)
        raise CompileError(f"unsupported expression: {type(node).__name__}", node)

    def check_attribute(self, node: ast.Attribute) -> ir.Expr:
        v = node.value
        if (node.attr == "__name__" and isinstance(v, ast.Call) and isinstance(v.func, ast.Name)
                and v.func.id == "type" and "type" not in self.ctx.types):
            if len(v.args) != 1 or v.keywords:
                raise CompileError("type() takes exactly one argument", v)
            obj = self.check_expr(v.args[0])
            self.no_int_taint(obj, v.args[0])
            t = obj.ty
            if isinstance(t, ClassT):
                return ir.TypeName(STR, obj, t.info)
            static = {I32: "int", I64: "int", F32: "float", F64: "float", BOOL: "bool", STR: "str"}
            if t in static:
                return ir.StrLit(STR, static[t])  # the names CPython reports under the snek shim
            if isinstance(t, ListT):
                return ir.StrLit(STR, "list")
            raise CompileError(f"type(x).__name__ of {t} is not supported (narrow it first)", node)
        if isinstance(v, ast.Call) and isinstance(v.func, ast.Name) and v.func.id == "type":
            raise CompileError("type() is only supported as type(x).__name__; use isinstance() for checks", node)
        obj = self.check_expr(v)
        if isinstance(obj.ty, ClassT) and node.attr in obj.ty.info.methods and node.attr not in obj.ty.info.fields:
            raise CompileError("bound methods are not first-class values yet; call the method directly", node)
        cls, f = self.field_of(obj, node)
        return ir.GetField(f.ty, obj, cls, f)

    def check_constant(self, node: ast.Constant, expected: Type | None) -> ir.Expr:
        v = node.value
        if isinstance(v, bool):
            return ir.Const(BOOL, v)
        if isinstance(v, (int, float)):
            return self.number(v, expected, node)
        if isinstance(v, str):
            return ir.StrLit(STR, self.utf8_text(v, node))
        if v is None:
            return ir.NoneLit(expected if isinstance(expected, OptionalT) else NONE)
        raise CompileError(f"unsupported literal {v!r}", node)

    def utf8_text(self, v: str, node: ast.AST) -> str:
        try:
            v.encode("utf-8")
        except UnicodeEncodeError:
            raise CompileError("string literal contains a lone surrogate, which is not valid UTF-8", node) from None
        return v

    def number(self, v: int | float, expected: Type | None, node: ast.AST) -> ir.Expr:
        if isinstance(v, float):
            return ir.Const(F32 if expected == F32 else F64, v)
        if isinstance(expected, Prim) and expected.is_float:
            return ir.Const(expected, float(v))
        if isinstance(expected, Prim) and expected.is_int:
            ty = expected
        elif INT_RANGE["i32"][0] <= v <= INT_RANGE["i32"][1]:
            ty = I32
        else:
            ty = I64
        lo, hi = INT_RANGE[ty.name]
        if not lo <= v <= hi:
            raise CompileError(f"integer literal {v} does not fit in {ty}", node)
        return ir.Const(ty, v)

    def load_name(self, node: ast.Name) -> ir.Expr:
        ctx = self.ctx
        n = node.id
        if n in ctx.types:
            if n not in ctx.flow.assigned:
                raise CompileError(f"local variable '{n}' may be used before it is assigned", node)
            return ctx.get(n)
        if n == "self" and ctx.is_init and not ctx.constructed:
            raise CompileError("`self` is used before the object exists", node)
        if n in self.funcs or n in self.classes:
            raise CompileError(f"'{n}' is not a first-class value (no function/class values yet)", node)
        raise CompileError(f"undefined name '{n}'", node)

    def arith(self, op: ast.operator, l: ir.Expr, r: ir.Expr, node: ast.AST) -> ir.Expr:
        info = ARITH_OPS.get(type(op))
        if info is None:
            sym = "**" if isinstance(op, ast.Pow) else type(op).__name__
            raise CompileError(f"operator {sym} is not supported yet", node)
        name, sym = info
        if name == "add" and l.ty == STR and r.ty == STR:
            return ir.StrConcat(STR, l, r)
        is_seq = lambda t: t == STR or isinstance(t, ListT)  # noqa: E731
        if name == "mul" and (is_seq(l.ty) or is_seq(r.ty)):
            seq_first = is_seq(l.ty)
            seq, n = (l, r) if seq_first else (r, l)
            if n.ty != I32:
                raise CompileError(f"a {seq.ty} can only be repeated by an i32, got {n.ty}", node)
            pre: list[ir.Stmt] = []
            if not seq_first:
                pre, n, seq = self.left_first(n, seq)
            rep_e = (ir.Call(STR, "rt.str_repeat", [seq, n]) if seq.ty == STR
                     else ir.ListOp(seq.ty, "repeat", seq, [n]))
            return self.seq(pre, rep_e)
        if name == "add" and isinstance(l.ty, ListT) and isinstance(r.ty, ListT):
            if l.ty != r.ty:
                raise CompileError(f"cannot concatenate {l.ty} and {r.ty}", node)
            return ir.ListOp(l.ty, "concat", l, [r])
        if STR in (l.ty, r.ty):
            raise CompileError(f"unsupported operands for {sym}: {l.ty} and {r.ty} (use str(x) or an f-string)", node)
        if not (isinstance(l.ty, Prim) and l.ty.is_numeric and isinstance(r.ty, Prim) and r.ty.is_numeric):
            raise CompileError(f"unsupported operands for {sym}: {l.ty} and {r.ty}", node)
        target = join_numeric(l.ty, r.ty)
        if target is None:
            raise CompileError(f"mixed operand types {l.ty} and {r.ty} for {sym}; convert one explicitly, "
                               f"e.g. {self.suggest(l.ty, r.ty)}", node)
        if name == "div" and target.is_int:
            if target != I32:
                raise CompileError("/ on i64 would lose precision as f64; use // for integer division or "
                                   "convert explicitly with f64(x)", node)
            # i32 widens losslessly to f64; "div_int" keeps CPython's int/int error message
            return ir.Binary(F64, "div_int", self.coerce(l, F64, node, arith=True),
                             self.coerce(r, F64, node, arith=True))
        if name in INT_ONLY_OPS and not target.is_int:
            raise CompileError(f"{sym} needs integer operands", node)
        return ir.Binary(target, name, self.coerce(l, target, node, arith=True),
                         self.coerce(r, target, node, arith=True))

    def suggest(self, a: Type, b: Type) -> str:
        if F64 in (a, b):
            return "f64(x)"
        return f"{max(a, b, key=str)}(x)"

    def check_boolop(self, node: ast.BoolOp) -> ir.Expr:
        op = "and" if isinstance(node.op, ast.And) else "or"
        saved = dict(self.ctx.flow.narrowed)
        result: ir.Expr | None = None
        for v in node.values:
            e = self.check_cond(v)
            result = e if result is None else ir.Logical(BOOL, op, result, e)
            t, f = self.facts(v)
            self.ctx.flow.narrowed.update(t if op == "and" else f)  # later operands see earlier facts
        self.ctx.flow.narrowed = saved
        assert result is not None
        return result

    def check_compare(self, node: ast.Compare) -> ir.Expr:
        if len(node.ops) != 1:
            raise CompileError("chained comparisons are not supported yet", node)
        op, left_n, right_n = node.ops[0], node.left, node.comparators[0]
        if isinstance(op, (ast.Is, ast.IsNot)):
            negate = isinstance(op, ast.IsNot)
            for a, b in ((left_n, right_n), (right_n, left_n)):
                if isinstance(b, ast.Constant) and b.value is None:
                    v = self.check_expr(a)
                    declared_optional = isinstance(v, ir.LocalGet) and isinstance(v.declared, OptionalT)
                    if not isinstance(v.ty, OptionalT) and not declared_optional:
                        raise CompileError(f"this is never None (its type is {v.ty}); declare it as "
                                           f"`{v.ty} | None` if it can be", a)
                    return ir.IsNull(BOOL, v, negate)
            l, r = self.check_expr(left_n), self.check_expr(right_n)
            if not (l.ty.is_ref and r.ty.is_ref):
                raise CompileError("`is` compares object identity; use == for numbers and bools", node)
            return ir.Compare(BOOL, "isnot" if negate else "is", l.ty, l, r)
        if isinstance(op, (ast.In, ast.NotIn)):
            e = self.check_in(left_n, right_n, node)
            return ir.Unary(BOOL, "not", e) if isinstance(op, ast.NotIn) else e
        name = CMP_OPS[type(op)]
        l, r = self.check_operands(left_n, right_n, None)
        if isinstance(strip_optional(l.ty), ListT) or isinstance(strip_optional(r.ty), ListT):
            if name not in ("eq", "ne"):
                raise CompileError("ordering comparisons (<, >, ...) on lists are not supported yet", node)
            if strip_optional(l.ty) != strip_optional(r.ty):
                raise CompileError(f"cannot compare {l.ty} with {r.ty}", node)
            eq = ir.ListOp(BOOL, "eq", l, [r])
            return ir.Unary(BOOL, "not", eq) if name == "ne" else eq
        if isinstance(l.ty, Prim) and l.ty.is_numeric and isinstance(r.ty, Prim) and r.ty.is_numeric:
            target = join_numeric(l.ty, r.ty)
            if target is None:
                raise CompileError(f"cannot compare {l.ty} with {r.ty}; convert one explicitly", node)
            return ir.Compare(BOOL, name, target, self.coerce(l, target, node, arith=True),
                              self.coerce(r, target, node, arith=True))
        if l.ty == BOOL and r.ty == BOOL and name in ("eq", "ne"):
            return ir.Compare(BOOL, name, BOOL, l, r)
        if l.ty == STR and r.ty == STR:
            return ir.Compare(BOOL, name, STR, l, r)
        if isinstance(strip_optional(l.ty), ClassT) and isinstance(strip_optional(r.ty), ClassT):
            raise CompileError("== on objects is not supported yet (no __eq__); use `is` for identity", node)
        raise CompileError(f"cannot compare {l.ty} with {r.ty}", node)

    def check_in(self, left_n: ast.expr, right_n: ast.expr, node: ast.AST) -> ir.Expr:
        r = self.check_expr(right_n)
        if isinstance(r.ty, OptionalT):
            raise CompileError(f"this may be None (type {r.ty}); check `is not None` first", right_n)
        if r.ty == STR:
            l = self.check_expr(left_n)
            if l.ty != STR:
                raise CompileError(f"'in <str>' needs a str on the left, got {l.ty}", left_n)
            pre, l, r = self.left_first(l, r)
            return self.seq(pre, ir.Call(BOOL, "rt.str_contains", [r, l]))
        if isinstance(r.ty, ListT):
            elem = r.ty.elem
            l = self.coerce(self.check_expr(left_n, elem), elem, left_n, arith=True)  # compared with ==
            pre, l, r = self.left_first(l, r)
            return self.seq(pre, ir.ListOp(BOOL, "contains", r, [l]))
        raise CompileError(f"`in` needs a str or a list on the right, got {r.ty}", node)

    def seq(self, prelude: list[ir.Stmt], e: ir.Expr) -> ir.Expr:
        return ir.Seq(e.ty, prelude, e) if prelude else e

    def check_call(self, node: ast.Call, expected: Type | None) -> ir.Expr:
        f, args = node.func, node.args
        if isinstance(f, ast.Name):
            n = f.id
            if n in self.ctx.types:
                raise CompileError(f"'{n}' is a variable, not a function", f)
            if self.builtin_mode and n in INTRINSICS:
                return self.check_intrinsic(n, node)
            if self.builtin_mode and n in self.bnodes:
                sig = self.builtin_func(n)
                prelude, bound = self.bind_args(sig, node, n)
                return self.seq(prelude, ir.Call(sig.ret, n, bound))
            if n in BUILTINS:
                if node.keywords:
                    raise CompileError(f"{n}() does not take keyword arguments here", node.keywords[0])
                if any(isinstance(a, ast.Starred) for a in args):
                    raise CompileError("*args in calls is not supported", node)
                return self.check_builtin_call(n, node, expected)
            if n in self.funcs:
                sig, _ = self.funcs[n]
                prelude, bound = self.bind_args(sig, node, n)
                return self.seq(prelude, ir.Call(sig.ret, n, bound))
            if n in self.classes:
                cls = self.classes[n]
                prelude, bound = self.bind_args(self.ctor_sig(cls), node, n)
                return self.seq(prelude, ir.New(ClassT(cls), cls, bound))
            raise CompileError(f"undefined function '{n}'", f)
        if isinstance(f, ast.Attribute):
            if is_super(f.value):
                return self.check_super_call(node, f)
            recv = self.check_expr(f.value)
            t = recv.ty
            if isinstance(t, OptionalT):
                raise CompileError(f"this may be None (type {t}); check `is not None` first", f.value)
            if isinstance(t, ClassT):
                m = t.info.methods.get(f.attr)
                if m is None:
                    what = "a field, not a method" if f.attr in t.info.fields else "not a method"
                    raise CompileError(f"'{f.attr}' is {what} of {t.info.name}", f)
                prelude, bound = self.bind_args(m.sig, node, f.attr)
                if prelude and not is_pure(recv):  # keep the receiver first in evaluation order
                    tmp = self.ctx.temp(recv.ty)
                    prelude.insert(0, ir.SetLocal(tmp, recv))
                    recv = self.ctx.get(tmp)
                return self.seq(prelude, ir.CallMethod(m.sig.ret, recv, t.info, m, bound))
            if t == STR:
                return self.check_str_method(recv, node, f)
            if isinstance(t, ListT):
                return self.check_list_method(recv, t, node, f)
            raise CompileError(f"{t} has no methods", f)
        raise CompileError("unsupported call target", node)

    def check_intrinsic(self, n: str, node: ast.Call) -> ir.Expr:
        params, ret, fn = INTRINSICS[n]
        if node.keywords or len(node.args) != len(params):
            raise CompileError(f"{n}() takes {len(params)} positional arguments", node)
        args = [self.coerce(self.check_expr(a, t), t, a) for a, t in zip(node.args, params)]
        # record which Unicode tables the program needs
        u = self.unicode
        if n in FLAG_MASK_ARG:
            m = const_int(args[FLAG_MASK_ARG[n]])
            u.flags |= unicode.ALL_FLAGS if m is None else m
        elif n == "__putcase":
            k = const_int(args[2])
            u.cases |= set(unicode.CASE_KINDS) if k is None else {k}
        elif n == "__decimal":
            u.decimal = True
        return ir.Call(ret, fn, args)

    def check_str_method(self, recv: ir.Expr, node: ast.Call, f: ast.Attribute) -> ir.Expr:
        name = f"__str_{f.attr}"
        if f.attr.startswith("_") or name not in self.bnodes:
            raise CompileError(f"str.{f.attr}() is not supported yet", f)
        sig = self.builtin_func(name)
        msig = FuncSig(sig.params[1:], sig.ret, sig.defaults[1:], max(sig.posonly - 1, 0))
        prelude, bound = self.bind_args(msig, node, f"str.{f.attr}")
        if prelude and not is_pure(recv):  # keep the receiver first in evaluation order
            tmp = self.ctx.temp(STR)
            prelude.insert(0, ir.SetLocal(tmp, recv))
            recv = self.ctx.get(tmp)
        return self.seq(prelude, ir.Call(sig.ret, name, [recv, *bound]))

    def check_list_method(self, recv: ir.Expr, t: ListT, node: ast.Call, f: ast.Attribute) -> ir.Expr:
        a, args, e = f.attr, node.args, t.elem
        if node.keywords:
            raise CompileError(f"list.{a}() does not take keyword arguments", node.keywords[0])

        def elem_arg(i: int, compared: bool = False) -> ir.Expr:
            # a value only compared with == (index, count, remove) mixes ints into floats like `in`
            return self.coerce(self.check_expr(args[i], e), e, args[i], arith=compared)

        if a == "append":
            self.arity(node, 1)
            return ir.ListAppend(NONE, recv, elem_arg(0))
        if a == "pop":
            if not args:
                return ir.ListPop(e, recv)
            self.arity(node, 1)
            return ir.ListOp(e, "pop_at", recv, [self.check_index(args[0])])
        if a == "insert":
            self.arity(node, 2)
            i = self.check_index(args[0])
            return ir.ListOp(NONE, "insert", recv, [i, elem_arg(1)])
        if a == "extend":
            self.arity(node, 1)
            return ir.ListOp(NONE, "extend", recv, [self.coerce(self.check_expr(args[0], t), t, args[0])])
        if a in ("remove", "count"):
            self.arity(node, 1)
            return ir.ListOp(I32 if a == "count" else NONE, a, recv, [elem_arg(0, compared=True)])
        if a == "index":
            if not 1 <= len(args) <= 3:
                raise CompileError(f"list.index() takes 1 to 3 arguments, got {len(args)}", node)
            if not self.has_repr(e):
                raise CompileError(f"list.index() on {t} is not supported: when the value is missing, CPython's "
                                   f"error message shows repr() of it, which snek cannot produce for {e}", f)
            v = elem_arg(0)  # not compared=True: a failure shows repr(v), and 2 must not print as 2.0
            start = self.check_index(args[1]) if len(args) > 1 else ir.Const(I32, 0)
            stop = self.check_index(args[2]) if len(args) > 2 else ir.Const(I32, INT_MAX)
            return ir.ListOp(I32, "index", recv, [v, start, stop])
        if a in ("reverse", "clear", "copy"):
            self.arity(node, 0)
            return ir.ListOp(t if a == "copy" else NONE, a, recv, [])
        raise CompileError(f"list.{a}() is not supported yet", f)

    def has_repr(self, t: Type) -> bool:
        while isinstance(t, ListT):
            t = t.elem
        return isinstance(t, Prim) or t in (STR, OptionalT(STR))

    def check_super_call(self, node: ast.Call, f: ast.Attribute) -> ir.Expr:
        ctx = self.ctx
        sup = f.value
        assert isinstance(sup, ast.Call)
        if sup.args or sup.keywords:
            raise CompileError("super() with arguments is not supported; write super()", sup)
        if f.attr == "__init__":
            raise CompileError("super().__init__(...) must be a statement of its own at the top level of "
                               "__init__ (not nested in if/loops/try or other expressions)", node)
        owner = ctx.owner
        if owner is None:
            raise CompileError("super() is only allowed inside methods", sup)
        if owner.base is None:
            raise CompileError(f"{owner.name} has no base class, so super().{f.attr}() has nothing to call", f)
        m = owner.base.methods.get(f.attr)
        if m is None:
            raise CompileError(f"{owner.base.name} has no method '{f.attr}'", f)
        if "self" not in ctx.types or "self" not in ctx.flow.assigned:
            raise CompileError("super() calls need `self`, which does not exist yet at this point of "
                               "__init__", node)
        prelude, bound = self.bind_args(m.sig, node, f"super().{f.attr}")
        # The base implementation is known statically: a direct call, no vtable.
        impl = owner.base.methods[f.attr].owner
        return self.seq(prelude, ir.Call(m.sig.ret, f"{impl.name}.{m.name}", [ctx.get("self"), *bound]))

    def check_builtin_call(self, n: str, node: ast.Call, expected: Type | None) -> ir.Expr:
        args = node.args
        if n == "print":
            raise CompileError("print() can only be used as a statement", node)
        if n == "len":
            self.arity(node, 1)
            v = self.check_expr(args[0])
            if isinstance(v.ty, ListT):
                return ir.ListLen(I32, v)
            if v.ty == STR:
                return ir.StrLen(I32, v)
            raise CompileError(f"len() of {v.ty}", node)
        if n == "str":
            self.arity(node, 1)
            return self.to_str(self.check_expr(args[0]), args[0])
        if n == "repr":
            self.arity(node, 1)
            return self.repr_of(self.check_expr(args[0]), args[0])
        if n == "ord":
            self.arity(node, 1)
            v = self.check_expr(args[0])
            if v.ty != STR:
                raise CompileError(f"ord() expected a str, got {v.ty}", args[0])
            return ir.Call(I32, "rt.ord", [v])
        if n == "chr":
            self.arity(node, 1)
            v = self.coerce(self.check_expr(args[0], I32), I32, args[0], "chr() argument")
            c = const_int(v)
            if c is None:
                self.unicode.dynamic = True  # any code point can occur: embed full Unicode tables
            elif 0 <= c < unicode.MAX_CP:
                self.unicode.extra.add(c)
            return ir.Call(STR, "rt.chr", [v])
        if n in NUMERIC:
            if n not in self.imported and not self.builtin_mode:
                raise CompileError(f"'{n}' must be imported: `from snek import {n}`", node.func)
            self.arity(node, 1)
            target = NUMERIC[n]
            v = self.check_expr(args[0], target)
            if v.ty == target:
                return v
            if v.ty == STR:
                if not target.is_int:
                    raise CompileError(f"{n}(str) is not supported yet (only i32() and i64() parse strings)", node)
                self.builtin_func("__parse_int")
                parsed = ir.Call(I64, "__parse_int", [v, ir.Const(I32, 32 if target == I32 else 64)])
                return parsed if target == I64 else ir.Convert(I32, parsed)
            if not (isinstance(v.ty, Prim)):
                raise CompileError(f"cannot convert {v.ty} to {target}", node)
            return ir.Convert(target, v)
        if n == "bool":
            self.arity(node, 1)
            v = self.check_expr(args[0])
            if v.ty == BOOL:
                return v
            if isinstance(v.ty, Prim):
                return ir.Compare(BOOL, "ne", v.ty, v, ir.Const(v.ty, 0))
            if v.ty == STR:
                return ir.Compare(BOOL, "ne", I32, ir.StrLen(I32, v), ir.Const(I32, 0))
            if isinstance(v.ty, ListT):
                return ir.Compare(BOOL, "ne", I32, ir.ListLen(I32, v), ir.Const(I32, 0))
            raise CompileError(f"bool() of {v.ty} is not supported (no implicit truthiness)", node)
        if n == "isinstance":
            self.arity(node, 2)
            v = self.check_expr(args[0])
            cn = args[1]
            if not (isinstance(cn, ast.Name) and cn.id in self.classes):
                raise CompileError("isinstance() needs a class defined in this module or a built-in "
                                   "exception class", cn)
            target = self.classes[cn.id]
            now = strip_optional(v.ty)
            # Judge "always False" against the declared type: after narrowing, an unrelated class is
            # just False at run time.
            st = strip_optional(v.declared if isinstance(v, ir.LocalGet) else v.ty)
            if not (isinstance(st, ClassT) and isinstance(now, ClassT)):
                raise CompileError(f"isinstance() needs an object of a class type, got {v.ty}", node)
            if now.info.is_subclass_of(target):  # always True, except for None
                if isinstance(v.ty, OptionalT):
                    return ir.IsNull(BOOL, v, True)
                return ir.Const(BOOL, True) if is_pure(v) else ir.Seq(BOOL, [ir.ExprStmt(v)], ir.Const(BOOL, True))
            if not target.is_subclass_of(st.info):
                raise CompileError(f"isinstance(x, {target.name}) is always False for a {st}", node)
            return ir.IsInstance(BOOL, v, target)
        if n == "abs":
            self.arity(node, 1)
            int_lit = is_num_literal(args[0]) and isinstance(literal_value(args[0]), int)
            v = self.check_expr(args[0], None if int_lit else expected)
            if not (isinstance(v.ty, Prim) and v.ty.is_numeric):
                raise CompileError(f"abs() of {v.ty}", node)
            return ir.Unary(v.ty, "abs", v)
        if n in ("min", "max"):
            self.arity(node, 2)
            l, r = self.check_operands(args[0], args[1], expected)
            target = join_numeric(l.ty, r.ty)
            if target is None:
                raise CompileError(f"{n}() needs two numbers of compatible types, got {l.ty} and {r.ty}", node)
            # CPython returns one argument unchanged (max(2, 1.5) is the int 2), so no int/float mixing
            return ir.Binary(target, n, self.coerce(l, target, args[0], f"{n}() argument"),
                             self.coerce(r, target, args[1], f"{n}() argument"))
        if n == "range":
            raise CompileError("range() is only supported directly in a for loop", node)
        if n in ("int", "float"):
            raise CompileError(f"use the fixed-width conversions (i32(), i64(), f64()) instead of {n}()", node)
        if n == "type":
            raise CompileError("type() is only supported as type(x).__name__; use isinstance() for checks", node)
        if n == "super":
            raise CompileError("super() must be followed by a method call: super().method(...)", node)
        raise CompileError(f"'{n}' cannot be called", node)

    def arity(self, node: ast.Call, n: int) -> None:
        if len(node.args) != n:
            name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "?")
            raise CompileError(f"{name}() takes {n} argument{'s' if n != 1 else ''}, got {len(node.args)}", node)

    def bind_args(self, sig: FuncSig, call: ast.Call, name: str) -> tuple[list[ir.Stmt], list[ir.Expr]]:
        """Match positional and keyword arguments to parameters and fill in defaults.

        Returns (prelude, args in parameter order). CPython evaluates arguments in source
        order; when keywords are written out of parameter order, the impure ones are
        evaluated into temporaries first (the prelude) so side effects happen in that order.
        """
        params = sig.params
        names = [p for p, _ in params]
        if any(isinstance(a, ast.Starred) for a in call.args):
            raise CompileError("*args in calls is not supported", call)
        if len(call.args) > len(params):
            raise CompileError(f"{name}() takes {len(params)} argument{'s' if len(params) != 1 else ''} but "
                               f"{len(call.args)} were given", call)

        def label(p: str) -> str:
            return "the exception message" if p.startswith("__") else f"argument '{p}'"

        slots: list[ir.Expr | None] = [None] * len(params)
        order: list[int] = []
        def user_type(p: str, t: Type) -> Type:
            # BaseException's hidden `__msg: str | None` (None = no argument) only accepts a str
            return t.inner if p.startswith("__") and isinstance(t, OptionalT) else t

        for i, a in enumerate(call.args):
            p, t = params[i]
            ut = user_type(p, t)
            slots[i] = self.coerce(self.check_expr(a, ut), ut, a, label(p))
            order.append(i)
        for kw in call.keywords:
            if kw.arg is None:
                raise CompileError("**kwargs in calls is not supported", kw)
            if kw.arg not in names or kw.arg.startswith("__"):
                raise CompileError(f"{name}() got an unexpected keyword argument '{kw.arg}'", kw)
            i = names.index(kw.arg)
            if i < sig.posonly:
                raise CompileError(f"{name}() takes '{kw.arg}' as a positional argument only", kw)
            if slots[i] is not None:
                raise CompileError(f"{name}() got multiple values for argument '{kw.arg}'", kw)
            p, t = params[i]
            slots[i] = self.coerce(self.check_expr(kw.value, t), t, kw.value, label(p))
            order.append(i)
        missing = [names[i] for i in range(len(params)) if slots[i] is None and sig.defaults[i] is None]
        if missing:
            listed = ", ".join(f"'{m}'" for m in missing)
            raise CompileError(f"{name}() missing required argument{'s' if len(missing) > 1 else ''}: {listed}",
                               call)
        prelude: list[ir.Stmt] = []
        if order != sorted(order):
            for i in order:
                e = slots[i]
                assert e is not None
                if not is_pure(e):
                    tmp = self.ctx.temp(params[i][1])
                    prelude.append(ir.SetLocal(tmp, e))
                    slots[i] = self.ctx.get(tmp)
        for i, (p, t) in enumerate(params):
            if slots[i] is None:
                d = sig.defaults[i]
                assert d is not None
                slots[i] = self.coerce(self.check_expr(d, t), t, d)
        return prelude, slots  # type: ignore[return-value]

    def check_index(self, node: ast.expr, what: str = "indices") -> ir.Expr:
        idx = self.check_expr(node, I32)
        if idx.ty != I32:
            raise CompileError(f"{what} are i32, got {idx.ty} (use i32(x))", node)
        return idx

    def check_slice(self, sl: ast.Slice) -> list[ir.Expr]:
        """[start, stop, step, flags]: flags bit 0/1 says start/stop was given (absent or None)."""
        flags = 0
        out: list[ir.Expr] = []
        for i, part in enumerate((sl.lower, sl.upper)):
            if part is None or (isinstance(part, ast.Constant) and part.value is None):
                out.append(ir.Const(I32, 0))
            else:
                out.append(self.check_index(part, "slice indices"))
                flags |= 1 << i
        no_step = sl.step is None or (isinstance(sl.step, ast.Constant) and sl.step.value is None)
        step = ir.Const(I32, 1) if no_step else self.check_index(sl.step, "slice indices")  # type: ignore[arg-type]
        return [out[0], out[1], step, ir.Const(I32, flags)]

    def left_first(self, left: ir.Expr, right: ir.Expr) -> tuple[list[ir.Stmt], ir.Expr, ir.Expr]:
        """Make `left` evaluate before `right` even where the call emits right first."""
        if is_pure(left) or is_pure(right):
            return [], left, right
        tmp = self.ctx.temp(left.ty)
        return [ir.SetLocal(tmp, left)], self.ctx.get(tmp), right

    def list_elem(self, lst: ir.Expr, node: ast.AST) -> Type:
        if isinstance(lst.ty, OptionalT):
            raise CompileError(f"this may be None (type {lst.ty}); check `is not None` first", node)
        if not isinstance(lst.ty, ListT):
            raise CompileError(f"expected a list, got {lst.ty}", node)
        return lst.ty.elem

    def field_of(self, obj: ir.Expr, node: ast.Attribute) -> tuple[ClassInfo, FieldInfo]:
        t = obj.ty
        if isinstance(t, OptionalT):
            raise CompileError(f"'{ast.unparse(node.value)}' may be None (type {t}); check `is not None` "
                               f"first (fields are not narrowed: copy to a local)", node.value)
        if not isinstance(t, ClassT):
            raise CompileError(f"{t} has no attribute '{node.attr}'", node)
        f = t.info.fields.get(node.attr)
        if f is None or f.name.startswith("__"):
            hint = "; read the message with str(e)" if self.is_exception(t) else ""
            raise CompileError(f"{t.info.name} has no field '{node.attr}'{hint}", node)
        return t.info, f

    def check_list(self, node: ast.List, expected: Type | None) -> ir.Expr:
        expected = strip_optional(expected) if expected is not None else None
        exp_elem = expected.elem if isinstance(expected, ListT) else None
        if not node.elts:
            if exp_elem is None:
                raise CompileError("cannot infer the element type of []; annotate it: `xs: list[i32] = []`", node)
            return ir.ListLit(expected, [])
        if exp_elem is None and any(isinstance(e, ast.List) and not e.elts for e in node.elts):
            # [["a"], []]: infer from the non-empty elements, then type the empty ones to match
            known = [self.check_expr(e) for e in node.elts if not (isinstance(e, ast.List) and not e.elts)]
            if known:
                exp_elem = reduce(lambda x, y: join_types(x, y) or x, [k.ty for k in known])
        items = [self.check_expr(e, exp_elem) for e in node.elts]
        elem = exp_elem
        if elem is None:
            elem = items[0].ty
            for it, e in zip(items[1:], node.elts[1:]):
                j = join_types(elem, it.ty)
                if j is None:
                    raise CompileError(f"list elements have incompatible types {elem} and {it.ty}", e)
                elem = j
            if elem == NONE:
                raise CompileError("cannot infer a list type from only None", node)
            items = [self.check_expr(e, elem) if is_num_literal(e) else it for it, e in zip(items, node.elts)]
        return ir.ListLit(ListT(elem), [self.coerce(it, elem, e) for it, e in zip(items, node.elts)])

    def check_fstring(self, node: ast.JoinedStr) -> ir.Expr:
        parts: list[ir.Expr] = []
        for v in node.values:
            if isinstance(v, ast.Constant):
                parts.append(ir.StrLit(STR, self.utf8_text(v.value, v)))
            elif isinstance(v, ast.FormattedValue):
                if v.format_spec is not None:
                    raise CompileError("f-string format specs are not supported yet", v)
                e = self.check_expr(v.value)
                if v.conversion == ord("r"):
                    parts.append(self.repr_of(e, v.value))
                elif v.conversion in (-1, ord("s")):
                    parts.append(self.to_str(e, v.value))
                else:
                    raise CompileError("the f-string !a (ascii) conversion is not supported", v)
        if not parts:
            return ir.StrLit(STR, "")
        out = parts[0]
        for p in parts[1:]:
            out = ir.StrConcat(STR, out, p)
        return out

    def check_ifexp(self, node: ast.IfExp, expected: Type | None) -> ir.Expr:
        cond = self.check_cond(node.test)
        t, f = self.facts(node.test)
        # a float context must not turn an int literal branch into a float (CPython keeps it an int)
        hint = None if isinstance(expected, Prim) and expected.is_float else expected
        saved = dict(self.ctx.flow.narrowed)
        self.ctx.flow.narrowed.update(t)
        a = self.check_expr(node.body, hint)
        self.ctx.flow.narrowed = dict(saved)
        self.ctx.flow.narrowed.update(f)
        b = self.check_expr(node.orelse, hint)
        self.ctx.flow.narrowed = saved
        ty = join_types(a.ty, b.ty)
        if ty is None:
            raise CompileError(f"branches have incompatible types {a.ty} and {b.ty}", node)
        if expected is not None and is_assignable(ty, expected):
            ty = expected
        # One int branch and one float branch: the value may be an int in CPython. That is fine
        # inside arithmetic; coerce() rejects it where it would be stored as a float.
        mixed = (isinstance(ty, Prim) and ty.is_float
                 and any(isinstance(x.ty, Prim) and x.ty.is_int for x in (a, b)))
        return ir.IfExpr(ty, cond, self.coerce(a, ty, node.body, arith=mixed),
                         self.coerce(b, ty, node.orelse, arith=mixed), mixed)

    def to_str(self, e: ir.Expr, node: ast.AST) -> ir.Expr:
        self.no_int_taint(e, node)
        t = e.ty
        if t == STR:
            return e
        if isinstance(t, Prim):
            return ir.ToStr(STR, e)
        if t == NONE:
            if isinstance(e, ir.NoneLit):
                return ir.StrLit(STR, "None")
            return ir.Seq(STR, [ir.ExprStmt(e)], ir.StrLit(STR, "None"))  # keep the call's side effects
        if t == OptionalT(STR):
            return ir.ToStr(STR, e)
        if self.is_exception(t):
            return ir.Call(STR, "rt.exc_str", [e])  # CPython's str(exception)
        if isinstance(t, ListT):
            inner = t.elem
            while isinstance(inner, ListT):
                inner = inner.elem
            if isinstance(inner, Prim) or inner in (STR, OptionalT(STR)):
                return ir.ToStr(STR, e)
            raise CompileError(f"converting {t} to str is not supported yet (lists of numbers, bools and "
                               f"strings are)", node)
        raise CompileError(f"converting {t} to str is not supported yet (no __str__/__repr__)", node)

    def repr_of(self, e: ir.Expr, node: ast.AST) -> ir.Expr:
        self.no_int_taint(e, node)
        t = e.ty
        if t == STR:
            return ir.Call(STR, "rt.str_repr", [e])
        if t == OptionalT(STR):
            return ir.Call(STR, "rt.opt_str_repr", [e])
        if self.is_exception(t):
            return ir.Call(STR, "rt.exc_repr", [e])
        if isinstance(t, OptionalT):
            raise CompileError(f"repr() of {t} is not supported yet; check `is not None` first", node)
        return self.to_str(e, node)  # numbers, bools, None and lists: str() is repr()

    def no_int_taint(self, e: ir.Expr, node: ast.AST, what: str = "value") -> None:
        """A conditional mixing an int and a float branch is an int on one path in CPython: fine as an
        arithmetic operand, but not where the value itself is kept, printed or inspected."""
        if int_tainted(e):
            raise CompileError(f"{what}: this can be an int at run time (one branch of a conditional "
                               f"expression is an int), and CPython would keep it an int; make both branches "
                               f"floats (1.0) or convert with f64(...)", node)

    def coerce(self, e: ir.Expr, target: Type, node: ast.AST, what: str = "value", arith: bool = False) -> ir.Expr:
        """Convert e to target where snek allows it implicitly.

        Ints become floats only inside arithmetic and comparisons (arith=True), where CPython's
        result is a float too. Anywhere a value is stored, passed or returned, CPython keeps an
        int an int (it prints as 2, not 2.0), so snek asks for 2.0 or f64(n) instead."""
        if (not arith and isinstance(target, Prim) and target.is_float and is_num_literal(node)
                and isinstance(literal_value(node), int)):
            v = literal_value(node)
            raise CompileError(f"{what}: write {float(v)!r} for a {target}; CPython keeps {v} an int (it would "
                               f"print as {v}, not {float(v)!r})", node)
        if not arith and isinstance(target, Prim) and target.is_float:
            self.no_int_taint(e, node, what)
        s = e.ty
        if s == target:
            return e
        if isinstance(s, Prim) and isinstance(target, Prim):
            if (s, target) in WIDEN:
                if s.is_int and target.is_float and not arith:
                    raise CompileError(f"{what}: expected {target}, got {s}. Ints only mix into floats inside "
                                       f"arithmetic; here CPython would keep the int (printing 2, not 2.0), so "
                                       f"convert explicitly with {target}(...)", node)
                if isinstance(e, ir.Const):
                    return ir.Const(target, float(e.value) if target.is_float else e.value)
                return ir.Convert(target, e)
            if s.is_numeric and target.is_numeric:
                raise CompileError(f"{what}: expected {target}, got {s}. Implicit {s} -> {target} conversion "
                                   f"could lose information; convert explicitly with {target}(...)", node)
            raise CompileError(f"{what}: expected {target}, got {s}", node)
        if isinstance(target, OptionalT) and (s == NONE or isinstance(e, ir.NoneLit)):
            return ir.NoneLit(target)
        if is_assignable(s, target):
            return e  # reference upcasts are implicit in WasmGC subtyping
        if isinstance(s, OptionalT) and is_assignable(s.inner, target):
            raise CompileError(f"{what} may be None (type {s}) but {target} is required; check "
                               f"`is not None` first", node)
        raise CompileError(f"{what}: expected {target}, got {s}", node)


def check_module(tree: ast.Module) -> ir.Module:
    return Checker(tree).run()
