"""Declaration pass + type checker. Elaborates Python AST into typed IR.

Two passes:
  1. declare  - collect classes (fields, vtable slots), function signatures and
                top-level code, so bodies can reference anything in the module.
  2. check    - type-check every body with local bidirectional inference,
                flow-sensitive narrowing and definite-assignment tracking, and
                lower it to ir.* as it goes.

Rules this enforces (see DESIGN.md for the reasoning):
  * signatures and class fields are annotated; locals are inferred
  * numbers are i32/i64/f32/f64; only lossless widening is implicit
  * `T | None` only for reference types; narrowing via `is not None`/isinstance
  * __init__ assigns every field before `self` is used (one struct.new)
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field

from . import ir
from .errors import CompileError
from .types import (
    BOOL,
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
    is_assignable,
    join_numeric,
    join_types,
    strip_optional,
)

INT_RANGE = {"i32": (-(2**31), 2**31 - 1), "i64": (-(2**63), 2**63 - 1)}

ALLOWED_IMPORTS = {
    "wpy": {"i32", "i64", "f32", "f64", "Final", "final"},
    "typing": {"Final", "Optional", "final"},
    "__future__": {"annotations"},
}

BUILTINS = {"print", "len", "str", "range", "isinstance", "abs", "min", "max", "bool",
            "i32", "i64", "f32", "f64", "int", "float", "list", "object", "super"}

ARITH_OPS = {
    ast.Add: ("add", "+"), ast.Sub: ("sub", "-"), ast.Mult: ("mul", "*"),
    ast.Div: ("div", "/"), ast.FloorDiv: ("floordiv", "//"), ast.Mod: ("mod", "%"),
    ast.BitAnd: ("bitand", "&"), ast.BitOr: ("bitor", "|"), ast.BitXor: ("bitxor", "^"),
    ast.LShift: ("shl", "<<"), ast.RShift: ("shr", ">>"),
}
INT_ONLY_OPS = {"bitand", "bitor", "bitxor", "shl", "shr"}

CMP_OPS = {ast.Eq: "eq", ast.NotEq: "ne", ast.Lt: "lt", ast.LtE: "le", ast.Gt: "gt", ast.GtE: "ge"}


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
    return out


def has_direct_break(stmts: list[ast.stmt]) -> bool:
    for s in stmts:
        if isinstance(s, ast.Break):
            return True
        if isinstance(s, (ast.While, ast.For)):
            continue  # a break in a nested loop exits that loop
        for child in ast.iter_child_nodes(s):
            if isinstance(child, ast.stmt) and has_direct_break([child]):
                return True
    return False


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
    def __init__(self, name: str, ret: Type, cls: ClassInfo | None = None, is_init: bool = False):
        self.name = name
        self.ret = ret
        self.cls = cls
        self.is_init = is_init
        self.constructed = not is_init
        self.params: list[tuple[str, Type]] = []
        self.locals: dict[str, Type] = {}
        self.types: dict[str, Type] = {}
        self.flow = Flow()
        self.loop_depth = 0
        self.ntemps = 0

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


# ---- the checker ---------------------------------------------------------------


class Checker:
    def __init__(self, tree: ast.Module):
        self.tree = tree
        self.classes: dict[str, ClassInfo] = {}
        self.class_order: list[ClassInfo] = []
        self.funcs: dict[str, tuple[FuncSig, ast.FunctionDef]] = {}
        self.main_stmts: list[ast.stmt] = []
        self.imported: set[str] = set()
        self.out: list[ir.Func] = []
        self.ctor_sigs: dict[ClassInfo, FuncSig] = {}
        self.ctx: FuncCtx = FuncCtx("<module>", NONE)

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
        return ir.Module(self.out, self.class_order, "__main__")

    def declare_module(self) -> None:
        pending: list[ast.FunctionDef] = []
        for s in self.tree.body:
            if isinstance(s, ast.ImportFrom):
                allowed = ALLOWED_IMPORTS.get(s.module or "")
                if allowed is None:
                    raise CompileError(f"import from '{s.module}' is not supported "
                                       f"(only wpy, typing and __future__)", s)
                for a in s.names:
                    if a.name not in allowed or a.asname:
                        raise CompileError(f"cannot import '{a.name}' from {s.module}", s)
                    self.imported.add(a.name)
            elif isinstance(s, ast.Import):
                raise CompileError("plain `import` is not supported; use `from wpy import i32, ...`", s)
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
            elif isinstance(s, ast.Expr):
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
        if name in self.classes or name in self.funcs:
            raise CompileError(f"'{name}' is already defined", node)
        if name in BUILTINS or name in ("str", "u8s", "rt", "lit") or name.startswith(("list_", "opt_")):
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
                    raise CompileError("base class must be a class defined in this module", b)
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
                sig = self.signature(item, is_method=True)
                inherited = cls.methods.get(name)
                if inherited:
                    if not sig.same_as(inherited.sig):
                        raise CompileError(
                            f"{cls.name}.{name} overrides {inherited.decl.name}.{name} with a different "
                            f"signature; overrides must match exactly", item)
                    cls.methods[name] = MethodInfo(name, inherited.sig, inherited.decl, inherited.slot, item, cls)
                else:
                    cls.methods[name] = MethodInfo(name, sig, cls, len(cls.methods), item, cls)
            elif isinstance(item, ast.Assign):
                raise CompileError("class attributes need a type annotation (they become instance "
                                   "fields): `name: T = value`", item)
            else:
                raise CompileError(f"unsupported statement in class body: {type(item).__name__}", item)

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
        if name.startswith("__"):
            raise CompileError("field names starting with '__' are reserved", item)
        if name in cls.fields:
            owner = cls.fields[name].owner.name
            raise CompileError(f"field '{name}' is already declared in {owner}", item)
        if item.value is not None and not (isinstance(item.value, ast.Constant) or is_num_literal(item.value)):
            raise CompileError("field defaults must be literals", item.value)
        cls.fields[name] = FieldInfo(name, ty, final, item.value, cls, len(cls.fields) + 1)

    def signature(self, node: ast.FunctionDef, is_method: bool) -> FuncSig:
        a = node.args
        if a.vararg or a.kwarg or a.kwonlyargs or a.posonlyargs:
            raise CompileError("*args, **kwargs and keyword-only parameters are not supported yet", node)
        if a.defaults:
            raise CompileError("default parameter values are not supported yet", a.defaults[0])
        if node.decorator_list and not is_method:
            raise CompileError("function decorators are not supported yet", node.decorator_list[0])
        args = list(a.args)
        if is_method:
            if not args or args[0].arg != "self":
                raise CompileError("methods must take `self` as their first parameter", node)
            args = args[1:]
        params: list[tuple[str, Type]] = []
        for arg in args:
            if arg.annotation is None:
                raise CompileError(f"parameter '{arg.arg}' needs a type annotation", arg)
            t = self.resolve_type(arg.annotation)
            if t == NONE:
                raise CompileError("a parameter cannot have type None", arg.annotation)
            if not arg.arg.isascii():
                raise CompileError("identifiers must be ASCII", arg)
            params.append((arg.arg, t))
        if node.returns is None:
            if node.name == "__init__":
                ret: Type = NONE
            else:
                raise CompileError(f"'{node.name}' needs a return annotation (`-> T` or `-> None`)", node)
        else:
            ret = self.resolve_type(node.returns)
        return FuncSig(params, ret)

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
                if n not in self.imported:
                    raise CompileError(f"'{n}' must be imported: `from wpy import {n}`", node)
                return NUMERIC[n]
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
        self.ctx = FuncCtx(f"{cls.name}.{m.name}", m.sig.ret, cls)
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
        # Overrides share the slot's types but may rename parameters.
        for p, t in self.signature(m.node, is_method=True).params:
            self.ctx.add_param(p, t)
        body = self.check_body(m.node.body, m.sig.ret, m.node)
        return ir.Func(f"{cls.name}.{m.name}", self.ctx.params, m.sig.ret, self.ctx.locals, body,
                       sig_type=f"{m.decl.name}.{m.name}.sig", self_cast=self_cast, nonnull_locals=nonnull)

    def check_main(self) -> ir.Func:
        self.ctx = FuncCtx("__main__", NONE)
        body, _ = self.check_block(self.main_stmts)
        return ir.Func("__main__", [], NONE, self.ctx.locals, body)

    def check_body(self, stmts: list[ast.stmt], ret: Type, node: ast.AST) -> list[ir.Stmt]:
        body, terminated = self.check_block(stmts)
        if ret != NONE and not terminated:
            raise CompileError(f"'{self.ctx.name}' can reach the end without returning a {ret}", node)
        return body

    def check_ctor(self, cls: ClassInfo) -> ir.Func:
        """Lower __init__ into `C.new(params) -> C`.

        WasmGC allocates a struct with all field values at once (struct.new), and
        non-null reference fields have no default. So the leading statements of
        __init__ may assign `self.f = <expr not using self>` (evaluated into
        temporaries) and run code that does not touch self; the first statement
        that uses self triggers the allocation, by which point every field must
        have a value. The rest of __init__ then runs on the real object.
        """
        sig = self.ctor_sig(cls)
        init = cls.effective_init()
        self.ctx = ctx = FuncCtx(f"{cls.name}.__init__", NONE, cls, is_init=True)
        for p, t in sig.params:
            ctx.add_param(p, t)
        stmts = init[0].body if init else []
        out: list[ir.Stmt] = []
        assigned: dict[str, str] = {}
        terminated = False
        for s in stmts:
            if not ctx.constructed:
                fa = self.self_field_assign(s)
                if fa is not None and not refers_to_self(fa[1]):
                    fname, vnode = fa
                    f = cls.fields.get(fname)
                    if f is None:
                        raise CompileError(f"{cls.name} has no field '{fname}'; declare it in the class "
                                           f"body: `{fname}: T`", s)
                    tmp = assigned.get(fname) or f"init.{fname}"
                    if fname not in assigned:
                        ctx.declare(tmp, f.ty)
                        assigned[fname] = tmp
                    out.append(ir.SetLocal(tmp, self.coerce(self.check_expr(vnode, f.ty), f.ty, vnode)))
                    ctx.flow.assigned.add(tmp)
                    continue
                if is_docstring(s):
                    continue
                if not refers_to_self(s):
                    stmts_ir, term = self.check_stmt(s)
                    out += stmts_ir
                    terminated = terminated or term
                    continue
                out.append(self.construct(cls, assigned, s))
            stmts_ir, term = self.check_stmt(s)
            out += stmts_ir
            terminated = terminated or term
        if not ctx.constructed:
            out.append(self.construct(cls, assigned, init[0] if init else cls.node))
        if not terminated:
            out.append(ir.Return(ctx.get("self")))
        return ir.Func(f"{cls.name}.new", ctx.params, ClassT(cls), ctx.locals, out)

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

    def construct(self, cls: ClassInfo, assigned: dict[str, str], at: ast.AST) -> ir.Stmt:
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
                f"{cls.name}.__init__ must assign {names} before `self` is used (or the field needs a "
                f"default in the class body). Objects are allocated in one step with every field set, "
                f"so field assignments have to come first.", at)
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
            return [self.check_return(s)], True
        if isinstance(s, ast.If):
            return self.check_if(s)
        if isinstance(s, ast.While):
            return self.check_while(s)
        if isinstance(s, ast.For):
            return self.check_for(s), False
        if isinstance(s, (ast.Break, ast.Continue)):
            if ctx.loop_depth == 0:
                raise CompileError(f"'{type(s).__name__.lower()}' outside a loop", s)
            return [ir.Break() if isinstance(s, ast.Break) else ir.Continue()], True
        if isinstance(s, ast.Pass):
            return [], False
        if isinstance(s, ast.Assert):
            return self.check_assert(s), False
        if isinstance(s, (ast.Raise, ast.Try)):
            raise CompileError("exceptions are not supported yet (planned: Wasm exception handling, "
                               "on by default in Wasmtime 47+)", s)
        if isinstance(s, (ast.FunctionDef, ast.ClassDef, ast.Lambda)):
            raise CompileError("nested functions and classes are not supported yet", s)
        if isinstance(s, (ast.Global, ast.Nonlocal)):
            raise CompileError("global/nonlocal are not supported", s)
        raise CompileError(f"unsupported statement: {type(s).__name__}", s)

    def check_print(self, call: ast.Call) -> ir.Stmt:
        if call.keywords:
            raise CompileError("print() keyword arguments (sep=, end=) are not supported yet", call)
        return ir.Print([self.to_str(self.check_expr(a), a) for a in call.args])

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
        if isinstance(target, ast.Attribute):
            obj = self.check_expr(target.value)
            cls, f = self.field_of(obj, target)
            if f.final:
                raise CompileError(f"'{f.name}' is Final and can only be assigned in __init__ before `self` "
                                   f"is used", target)
            return [ir.SetField(obj, cls, f, self.coerce(self.check_expr(value, f.ty), f.ty, value))]
        if isinstance(target, ast.Subscript):
            lst = self.check_expr(target.value)
            elem = self.list_elem(lst, target.value)
            idx = self.check_index(target.slice)
            return [ir.SetIndex(lst, idx, self.coerce(self.check_expr(value, elem), elem, value))]
        if isinstance(target, (ast.Tuple, ast.List)):
            raise CompileError("tuple unpacking is not supported yet", target)
        raise CompileError("unsupported assignment target", target)

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
            new = self.arith(s.op, cur, self.check_operand(s.value, f.ty), s)
            return [ir.SetLocal(tmp, obj), ir.SetField(ctx.get(tmp), cls, f, self.coerce(new, f.ty, s))]
        if isinstance(t, ast.Subscript):
            lst = self.check_expr(t.value)
            elem = self.list_elem(lst, t.value)
            ltmp, itmp = ctx.temp(lst.ty), ctx.temp(I32)
            idx = self.check_index(t.slice)
            cur = ir.ListGet(elem, ctx.get(ltmp), ctx.get(itmp))
            new = self.arith(s.op, cur, self.check_operand(s.value, elem), s)
            return [ir.SetLocal(ltmp, lst), ir.SetLocal(itmp, idx),
                    ir.SetIndex(ctx.get(ltmp), ctx.get(itmp), self.coerce(new, elem, s))]
        raise CompileError("unsupported augmented assignment target", t)

    def check_return(self, s: ast.Return) -> ir.Stmt:
        ctx = self.ctx
        if ctx.is_init:
            if s.value is not None:
                raise CompileError("__init__ cannot return a value", s)
            if not ctx.constructed:
                raise CompileError("return in __init__ before all fields are assigned", s)
            return ir.Return(ctx.get("self"))
        if ctx.ret == NONE:
            if s.value is not None and not (isinstance(s.value, ast.Constant) and s.value.value is None):
                raise CompileError(f"'{ctx.name}' is declared `-> None` but returns a value", s)
            return ir.Return(None)
        if s.value is None:
            raise CompileError(f"'{ctx.name}' must return a {ctx.ret}", s)
        return ir.Return(self.coerce(self.check_expr(s.value, ctx.ret), ctx.ret, s.value))

    def check_assert(self, s: ast.Assert) -> list[ir.Stmt]:
        cond = self.check_cond(s.test)
        msg: ir.Expr = ir.StrLit(STR, "AssertionError")
        if s.msg is not None:
            msg = ir.StrConcat(STR, ir.StrLit(STR, "AssertionError: "), self.to_str(self.check_expr(s.msg), s.msg))
        true_facts, _ = self.facts(s.test)
        self.ctx.flow.narrowed.update(true_facts)  # a failed assert never falls through
        return [ir.If(ir.Unary(BOOL, "not", cond), [ir.Panic(msg)], [])]

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
                raise CompileError("iterating over a str is not supported yet (pending the code point vs "
                                   "byte indexing decision)", it)
            elem = self.list_elem(seq, it)
            self.bind_loop_var(var, elem, s.target)
            ltmp, itmp = ctx.temp(seq.ty), ctx.temp(I32)
            head = [ir.SetLocal(var, ir.ListGet(elem, ctx.get(ltmp), ctx.get(itmp)))]
            ctx.flow = loop_flow.copy()  # the loop variable is only assigned inside the body
            ctx.flow.assigned.add(var)
            body = self.check_loop_body(s.body, head)
            cond = ir.Compare(BOOL, "lt", I32, ctx.get(itmp), ir.ListLen(I32, ctx.get(ltmp)))
            step = [ir.SetLocal(itmp, ir.Binary(I32, "add", ctx.get(itmp), ir.Const(I32, 1)))]
            stmts = [ir.SetLocal(ltmp, seq), ir.SetLocal(itmp, ir.Const(I32, 0)), ir.Loop(cond, body, step)]
        ctx.flow = loop_flow.copy()
        return stmts

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
        ctx.flow.assigned.add(var)
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
        return self.check_expr(node)

    def check_operands(self, left: ast.expr, right: ast.expr, expected: Type | None) -> tuple[ir.Expr, ir.Expr]:
        num_expected = expected if isinstance(expected, Prim) and expected.is_numeric else None
        if is_num_literal(left) and not is_num_literal(right):
            r = self.check_expr(right, num_expected)
            return self.check_operand(left, r.ty), r
        l = self.check_expr(left, num_expected)
        return l, self.check_operand(right, l.ty)

    def check_expr(self, node: ast.expr, expected: Type | None = None) -> ir.Expr:
        ctx = self.ctx
        if isinstance(node, ast.Constant):
            return self.check_constant(node, expected)
        if isinstance(node, ast.Name):
            return self.load_name(node)
        if isinstance(node, ast.UnaryOp):
            if is_num_literal(node):
                return self.number(literal_value(node), expected, node)
            if isinstance(node.op, ast.Not):
                return ir.Unary(BOOL, "not", self.check_cond(node.operand))
            v = self.check_expr(node.operand, expected)
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
            obj = self.check_expr(node.value)
            if isinstance(obj.ty, ClassT) and node.attr in obj.ty.info.methods and node.attr not in obj.ty.info.fields:
                raise CompileError("bound methods are not first-class values yet; call the method directly", node)
            cls, f = self.field_of(obj, node)
            return ir.GetField(f.ty, obj, cls, f)
        if isinstance(node, ast.Subscript):
            if isinstance(node.slice, ast.Slice):
                raise CompileError("slicing is not supported yet", node)
            lst = self.check_expr(node.value)
            if lst.ty == STR:
                raise CompileError("indexing a str is not supported yet (pending the code point vs byte "
                                   "indexing decision)", node)
            elem = self.list_elem(lst, node.value)
            return ir.ListGet(elem, lst, self.check_index(node.slice))
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

    def check_constant(self, node: ast.Constant, expected: Type | None) -> ir.Expr:
        v = node.value
        if isinstance(v, bool):
            return ir.Const(BOOL, v)
        if isinstance(v, (int, float)):
            return self.number(v, expected, node)
        if isinstance(v, str):
            return ir.StrLit(STR, v)
        if v is None:
            return ir.NoneLit(expected if isinstance(expected, OptionalT) else NONE)
        raise CompileError(f"unsupported literal {v!r}", node)

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
            target = F64  # i32 widens losslessly to f64
        if name in INT_ONLY_OPS and not target.is_int:
            raise CompileError(f"{sym} needs integer operands", node)
        return ir.Binary(target, name, self.coerce(l, target, node), self.coerce(r, target, node))

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
                    if not isinstance(v.ty, OptionalT):
                        raise CompileError(f"this is never None (its type is {v.ty}); declare it as "
                                           f"`{v.ty} | None` if it can be", a)
                    return ir.IsNull(BOOL, v, negate)
            l, r = self.check_expr(left_n), self.check_expr(right_n)
            if not (l.ty.is_ref and r.ty.is_ref):
                raise CompileError("`is` compares object identity; use == for numbers and bools", node)
            return ir.Compare(BOOL, "isnot" if negate else "is", l.ty, l, r)
        if isinstance(op, (ast.In, ast.NotIn)):
            raise CompileError("`in` is not supported yet", node)
        name = CMP_OPS[type(op)]
        l, r = self.check_operands(left_n, right_n, None)
        if isinstance(l.ty, Prim) and l.ty.is_numeric and isinstance(r.ty, Prim) and r.ty.is_numeric:
            target = join_numeric(l.ty, r.ty)
            if target is None:
                raise CompileError(f"cannot compare {l.ty} with {r.ty}; convert one explicitly", node)
            return ir.Compare(BOOL, name, target, self.coerce(l, target, node), self.coerce(r, target, node))
        if l.ty == BOOL and r.ty == BOOL and name in ("eq", "ne"):
            return ir.Compare(BOOL, name, BOOL, l, r)
        if l.ty == STR and r.ty == STR:
            return ir.Compare(BOOL, name, STR, l, r)
        if isinstance(strip_optional(l.ty), ClassT) and isinstance(strip_optional(r.ty), ClassT):
            raise CompileError("== on objects is not supported yet (no __eq__); use `is` for identity", node)
        raise CompileError(f"cannot compare {l.ty} with {r.ty}", node)

    def check_call(self, node: ast.Call, expected: Type | None) -> ir.Expr:
        if node.keywords:
            raise CompileError("keyword arguments are not supported yet", node.keywords[0])
        f, args = node.func, node.args
        if isinstance(f, ast.Name):
            n = f.id
            if n in self.ctx.types:
                raise CompileError(f"'{n}' is a variable, not a function", f)
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
            if n in NUMERIC:
                if n not in self.imported:
                    raise CompileError(f"'{n}' must be imported: `from wpy import {n}`", f)
                self.arity(node, 1)
                target = NUMERIC[n]
                v = self.check_expr(args[0], target)
                if v.ty == target:
                    return v
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
                raise CompileError(f"bool() of {v.ty} is not supported (no implicit truthiness)", node)
            if n == "isinstance":
                self.arity(node, 2)
                v = self.check_expr(args[0])
                cn = args[1]
                if not (isinstance(cn, ast.Name) and cn.id in self.classes):
                    raise CompileError("isinstance() needs a class defined in this module", cn)
                target = self.classes[cn.id]
                st = strip_optional(v.ty)
                if not (isinstance(st, ClassT) and target.is_subclass_of(st.info)):
                    raise CompileError(f"isinstance(x, {target.name}) is always False for a {v.ty}", node)
                return ir.IsInstance(BOOL, v, target)
            if n == "abs":
                self.arity(node, 1)
                v = self.check_expr(args[0], expected)
                if not (isinstance(v.ty, Prim) and v.ty.is_numeric):
                    raise CompileError(f"abs() of {v.ty}", node)
                return ir.Unary(v.ty, "abs", v)
            if n in ("min", "max"):
                self.arity(node, 2)
                l, r = self.check_operands(args[0], args[1], expected)
                target = join_numeric(l.ty, r.ty)
                if target is None:
                    raise CompileError(f"{n}() needs two numbers of compatible types, got {l.ty} and {r.ty}", node)
                return ir.Binary(target, n, self.coerce(l, target, node), self.coerce(r, target, node))
            if n == "range":
                raise CompileError("range() is only supported directly in a for loop", node)
            if n in ("int", "float"):
                raise CompileError(f"use the fixed-width conversions (i32(), i64(), f64()) instead of {n}()", node)
            if n in self.funcs:
                sig, _ = self.funcs[n]
                return ir.Call(sig.ret, n, self.check_args(sig, args, node, n))
            if n in self.classes:
                cls = self.classes[n]
                return ir.New(ClassT(cls), cls, self.check_args(self.ctor_sig(cls), args, node, n))
            raise CompileError(f"undefined function '{n}'", f)
        if isinstance(f, ast.Attribute):
            if isinstance(f.value, ast.Call) and isinstance(f.value.func, ast.Name) and f.value.func.id == "super":
                raise CompileError("super() is not supported yet; in __init__, assign inherited fields directly", f)
            recv = self.check_expr(f.value)
            t = recv.ty
            if isinstance(t, OptionalT):
                raise CompileError(f"this may be None (type {t}); check `is not None` first", f.value)
            if isinstance(t, ClassT):
                m = t.info.methods.get(f.attr)
                if m is None:
                    what = "a field, not a method" if f.attr in t.info.fields else "not a method"
                    raise CompileError(f"'{f.attr}' is {what} of {t.info.name}", f)
                return ir.CallMethod(m.sig.ret, recv, t.info, m, self.check_args(m.sig, args, node, f.attr))
            if isinstance(t, ListT):
                if f.attr == "append":
                    self.arity(node, 1)
                    return ir.ListAppend(NONE, recv, self.coerce(self.check_expr(args[0], t.elem), t.elem, args[0]))
                if f.attr == "pop":
                    self.arity(node, 0)
                    return ir.ListPop(t.elem, recv)
                raise CompileError(f"list.{f.attr}() is not supported yet (append and pop are)", f)
            if t == STR:
                raise CompileError("str methods are not supported yet", f)
            raise CompileError(f"{t} has no methods", f)
        raise CompileError("unsupported call target", node)

    def arity(self, node: ast.Call, n: int) -> None:
        if len(node.args) != n:
            name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "?")
            raise CompileError(f"{name}() takes {n} argument{'s' if n != 1 else ''}, got {len(node.args)}", node)

    def check_args(self, sig: FuncSig, args: list[ast.expr], node: ast.AST, name: str) -> list[ir.Expr]:
        if len(args) != len(sig.params):
            raise CompileError(f"{name}() takes {len(sig.params)} argument(s), got {len(args)}", node)
        return [self.coerce(self.check_expr(a, t), t, a, f"argument '{p}'") for a, (p, t) in zip(args, sig.params)]

    def check_index(self, node: ast.expr) -> ir.Expr:
        idx = self.check_expr(node, I32)
        if idx.ty != I32:
            raise CompileError(f"list indices are i32, got {idx.ty} (use i32(x))", node)
        return idx

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
        if f is None:
            raise CompileError(f"{t.info.name} has no field '{node.attr}'", node)
        return t.info, f

    def check_list(self, node: ast.List, expected: Type | None) -> ir.Expr:
        exp_elem = expected.elem if isinstance(expected, ListT) else None
        if not node.elts:
            if exp_elem is None:
                raise CompileError("cannot infer the element type of []; annotate it: `xs: list[i32] = []`", node)
            return ir.ListLit(expected, [])
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
                parts.append(ir.StrLit(STR, v.value))
            elif isinstance(v, ast.FormattedValue):
                if v.conversion != -1 or v.format_spec is not None:
                    raise CompileError("f-string conversions and format specs are not supported yet", v)
                parts.append(self.to_str(self.check_expr(v.value), v.value))
        if not parts:
            return ir.StrLit(STR, "")
        out = parts[0]
        for p in parts[1:]:
            out = ir.StrConcat(STR, out, p)
        return out

    def check_ifexp(self, node: ast.IfExp, expected: Type | None) -> ir.Expr:
        cond = self.check_cond(node.test)
        t, f = self.facts(node.test)
        saved = dict(self.ctx.flow.narrowed)
        self.ctx.flow.narrowed.update(t)
        a = self.check_expr(node.body, expected)
        self.ctx.flow.narrowed = dict(saved)
        self.ctx.flow.narrowed.update(f)
        b = self.check_expr(node.orelse, expected)
        self.ctx.flow.narrowed = saved
        ty = join_types(a.ty, b.ty)
        if ty is None:
            raise CompileError(f"branches have incompatible types {a.ty} and {b.ty}", node)
        if expected is not None and is_assignable(ty, expected):
            ty = expected
        return ir.IfExpr(ty, cond, self.coerce(a, ty, node.body), self.coerce(b, ty, node.orelse))

    def to_str(self, e: ir.Expr, node: ast.AST) -> ir.Expr:
        t = e.ty
        if t == STR:
            return e
        if isinstance(t, Prim):
            return ir.ToStr(STR, e)
        if t == NONE:
            return ir.StrLit(STR, "None")
        if t == OptionalT(STR):
            return ir.ToStr(STR, e)
        if isinstance(t, ListT):
            inner = t.elem
            while isinstance(inner, ListT):
                inner = inner.elem
            if isinstance(inner, Prim):
                return ir.ToStr(STR, e)
            raise CompileError(f"converting {t} to str is not supported yet (only lists of numbers/bools)", node)
        raise CompileError(f"converting {t} to str is not supported yet (no __str__/__repr__)", node)

    def coerce(self, e: ir.Expr, target: Type, node: ast.AST, what: str = "value") -> ir.Expr:
        s = e.ty
        if s == target:
            return e
        if isinstance(s, Prim) and isinstance(target, Prim):
            if (s, target) in WIDEN:
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
