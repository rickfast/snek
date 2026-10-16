"""Lexical scope analysis and closure conversion, before suspension lowering.

Only bindings referenced by a child scope get cells. Environments contain cell
references, never copies of values. Nested bodies are checked after their parent
body, so a capture may be typed by an assignment following the def statement.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, field, fields, replace

from . import ir
from .errors import CompileError
from .types import CallableT, CellT, EnvT, FuncSig, NONE, OptionalT, Type, ClassT


FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)


def scope_walk(nodes):
    """Walk one lexical scope, including but not descending into child scopes."""
    for node in nodes:
        yield node
        if isinstance(node, ast.AnnAssign):
            yield from scope_walk([node.target] + ([node.value] if node.value is not None else []))
        elif not isinstance(node, (*FUNCTIONS, ast.ClassDef)):
            yield from scope_walk(ast.iter_child_nodes(node))


def body_nodes(node):
    return [node.body] if isinstance(node, ast.Lambda) else node.body


@dataclass
class Scope:
    bound: set[str] = field(default_factory=set)
    free: set[str] = field(default_factory=set)
    cells: set[str] = field(default_factory=set)
    nonlocal_: set[str] = field(default_factory=set)
    children: dict[int, Scope] = field(default_factory=dict)


def analyze_scope(node, outer: set[str] | None = None) -> Scope:
    outer = outer or set()
    nodes = list(scope_walk(body_nodes(node)))
    scope = Scope()
    args = getattr(node, "args", None)
    params = {a.arg for a in [*args.posonlyargs, *args.args, *args.kwonlyargs]} if args else set()
    if args and len(params) != len(args.posonlyargs) + len(args.args) + len(args.kwonlyargs):
        raise CompileError("duplicate parameter name", node)
    scope.bound |= params
    used = set()
    for n in nodes:
        if isinstance(n, ast.Global):
            raise CompileError("global is not supported; keep state in objects or lexical closures", n)
        if isinstance(n, ast.Nonlocal):
            for name in n.names:
                if name not in outer:
                    raise CompileError(f"no binding for nonlocal '{name}' found", n)
                if name in params:
                    raise CompileError(f"parameter '{name}' cannot be nonlocal", n)
                # Python rejects a declaration after an earlier use or binding.
                if name in used or name in scope.bound:
                    raise CompileError(f"nonlocal '{name}' must precede its use or assignment", n)
                scope.nonlocal_.add(name)
        elif isinstance(n, ast.Name):
            if isinstance(n.ctx, (ast.Store, ast.Del)):
                scope.bound.add(n.id)
            else:
                used.add(n.id)
        elif isinstance(n, ast.ExceptHandler) and n.name:
            scope.bound.add(n.name)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            scope.bound.add(n.name)
    scope.bound -= scope.nonlocal_
    scope.free = (used - scope.bound) & outer | scope.nonlocal_
    for n in nodes:
        if isinstance(n, FUNCTIONS):
            child = analyze_scope(n, outer | scope.bound)
            scope.children[id(n)] = child
            scope.cells |= child.free & scope.bound
            scope.free |= child.free - scope.bound
    return scope


def callable_type(sig: FuncSig) -> CallableT:
    return CallableT(tuple(t for _, t in sig.params), sig.ret)


class ClosureChecker:
    def prepare_scope(self, node, scope: Scope | None = None, parent=None):
        ctx = self.ctx
        ctx.scope = scope or analyze_scope(node)
        ctx.parent = parent
        ctx.cells = ctx.scope.cells | ctx.scope.free
        ctx.free = set(ctx.scope.free)
        for name in sorted(ctx.free):
            if parent is None or name not in parent.types:
                raise CompileError(f"cannot infer captured variable '{name}'; add a local type annotation", node)
            ctx.types[name] = parent.types[name]
            ctx.flow.assigned.add(name)  # reads still check the cell's runtime bound flag
        # Signatures and explicit local annotations are known throughout the scope;
        # values remain subject to definite assignment at the statement where used.
        for n in scope_walk(body_nodes(node)):
            if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
                name = n.target.id
                if name in ctx.free:
                    raise CompileError("a nonlocal variable cannot be annotated", n)
                ty = self.resolve_type(n.annotation)
                if ty == NONE:
                    raise CompileError("a variable cannot have type None", n)
                if name in ctx.types and ctx.types[name] != ty:
                    raise CompileError(f"'{name}' was already declared as {ctx.types[name]}", n)
                if name not in ctx.types:
                    ctx.declare(name, ty)
            elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if not n.name.isascii():
                    raise CompileError("identifiers must be ASCII", n)
                sig = self.signature(n, is_method=False)
                ctx.nested_sigs[id(n)] = sig
                ty = callable_type(sig)
                if n.name in ctx.types and ctx.types[n.name] != ty:
                    raise CompileError(f"'{n.name}' was already declared as {ctx.types[n.name]}", n)
                if n.name not in ctx.types:
                    ctx.declare(n.name, ty)

    def closure_name(self, node) -> str:
        ctx = self.ctx
        if ctx.is_init or ctx.scope is None:
            raise CompileError("closures in __init__ are not supported yet; use an ordinary method", node)
        if ctx.name == "__main__" and ctx.scope.children[id(node)].free:
            raise CompileError("module state cannot be captured; put the enclosing code in a function", node)
        name = f"closure.{self.nclosures}"
        self.nclosures += 1
        return name

    def defer_closure(self, node, sig: FuncSig) -> ir.Closure:
        value = ir.Closure(callable_type(sig), self.closure_name(node), [])
        self.ctx.pending_closures.append((node, sig, value))
        return value

    def check_lambda(self, node: ast.Lambda, expected: Type | None) -> ir.Expr:
        if isinstance(expected, OptionalT):
            expected = expected.inner
        if not isinstance(expected, CallableT):
            raise CompileError("lambda needs a Callable[[...], T] context (annotate the variable or parameter)", node)
        self.validate_lambda_args(node, len(expected.params))
        sig = FuncSig([(a.arg, t) for a, t in zip(node.args.args, expected.params)], expected.ret)
        return self.defer_closure(node, sig)

    def validate_lambda_args(self, node: ast.Lambda, arity: int) -> None:
        a = node.args
        if a.defaults or a.vararg or a.kwarg or a.kwonlyargs or a.posonlyargs:
            raise CompileError("lambda defaults, *args, **kwargs and keyword-only/positional-only parameters are not supported", node)
        if len(a.args) != arity:
            raise CompileError("lambda parameter count does not match its Callable type", node)
        if any(not p.arg.isascii() for p in a.args):
            raise CompileError("identifiers must be ASCII", node)

    def check_key_lambda(self, node: ast.Lambda, elem: Type) -> ir.Closure:
        """The collection supplies the parameter; infer the comparable result.

        Unlike fully context-typed lambdas, this body must be checked at the call
        site. A captured variable therefore needs a known type at that point.
        """
        from .check import FuncCtx
        self.validate_lambda_args(node, 1)
        name = self.closure_name(node)
        parent = self.ctx
        self.ctx = child = FuncCtx(name, NONE)
        try:
            child.add_param("closure.env", EnvT())
            child.add_param(node.args.args[0].arg, elem)
            self.prepare_scope(node, parent.scope.children[id(node)], parent)
            result = self.check_expr(node.body)
            self.no_int_taint(result, node.body, "key result")
            ty = CallableT((elem,), result.ty)
            child.ret = result.ty
            func = ir.Func(name, child.params, result.ty, child.locals, [ir.Return(result)], closure_type=ty)
            self.out.append(self.finish_closures(func))
        finally:
            self.ctx = parent
        return ir.Closure(ty, name, [cell_ref(n, parent.types[n]) for n in sorted(child.free)])

    def check_callable_call(self, node: ast.Call, value: ir.Expr) -> ir.Expr:
        ty = value.ty
        if not isinstance(ty, CallableT):
            if isinstance(ty, OptionalT) and isinstance(ty.inner, CallableT):
                raise CompileError(f"callable may be None (type {ty}); check `is not None` first", node)
            raise CompileError(f"{ty} is not callable", node)
        if node.keywords or any(isinstance(a, ast.Starred) for a in node.args):
            raise CompileError("Callable values take positional arguments only (no keywords or *args)", node)
        if len(node.args) != len(ty.params):
            raise CompileError(f"Callable expects {len(ty.params)} arguments, got {len(node.args)}", node)
        args = [self.coerce(self.check_expr(a, t), t, a) for a, t in zip(node.args, ty.params)]
        return ir.CallValue(ty.ret, value, args)

    def finish_closures(self, func: ir.Func) -> ir.Func:
        from .check import FuncCtx
        ctx = self.ctx
        for node, sig, value in ctx.pending_closures:
            self.ctx = child = FuncCtx(value.func, sig.ret)
            child.add_param("closure.env", EnvT())
            for p, t in sig.params:
                child.add_param(p, t)
            self.prepare_scope(node, ctx.scope.children[id(node)], ctx)
            machine = False
            if isinstance(node, ast.Lambda):
                if (sig.ret == NONE and isinstance(node.body, ast.Call)
                        and isinstance(node.body.func, ast.Name) and node.body.func.id == "print"
                        and "print" not in child.types):
                    body = self.check_print(node.body) + [ir.Return(None)]
                else:
                    result = self.coerce(self.check_expr(node.body, sig.ret), sig.ret, node.body)
                    body = (([] if isinstance(result, ir.NoneLit) else [ir.ExprStmt(result)]) + [ir.Return(None)]
                            if sig.ret == NONE else [ir.Return(result)])
            else:
                machine = self.suspension_context(node, sig.ret)
                body = self.check_body(node.body, child.ret, node)
            nested = ir.Func(value.func, child.params, sig.ret, child.locals, body, machine=machine,
                             closure_type=callable_type(sig))
            self.out.append(self.finish_closures(nested))
            self.ctx = ctx
            value.captures = [cell_ref(n, ctx.types[n]) for n in sorted(child.free)]
        return lower_cells(func, ctx, self.exc_base)


def cell_ref(name: str, ty: Type) -> ir.LocalGet:
    t = CellT(ty)
    return ir.LocalGet(t, "cell." + name, t)


def lower_cells(func: ir.Func, ctx, exc_base) -> ir.Func:
    cells = {n: ctx.types[n] for n in ctx.cells if n in ctx.types}
    if not cells:
        return func
    prefix = []
    params = dict(func.params)
    env = ir.LocalGet(EnvT(), "closure.env", EnvT())
    layout = EnvT(tuple(CellT(ctx.types[n]) for n in sorted(ctx.free)))
    for name, ty in sorted(cells.items()):
        ref = cell_ref(name, ty)
        func.locals[ref.name] = ref.ty
        if name in ctx.free:
            prefix.append(ir.SetLocal(ref.name, ir.EnvGet(ref.ty, env, layout, sorted(ctx.free).index(name))))
        else:
            prefix.append(ir.SetLocal(ref.name, ir.CellNew(ref.ty)))
            if name in params:
                prefix.append(ir.CellSet(ref, ir.LocalGet(ty, name, ty)))
        if name not in params:
            func.locals.pop(name, None)

    def rewrite(value):
        if isinstance(value, ir.LocalGet) and value.name in cells:
            return ir.CellGet(value.ty, cell_ref(value.name, cells[value.name]), value.name, value.name in ctx.free)
        if isinstance(value, ir.SetLocal) and value.name in cells:
            return ir.CellSet(cell_ref(value.name, cells[value.name]), rewrite(value.value))
        if isinstance(value, ir.Handler) and value.var in cells:
            # CPython clears an except-as binding on every exit, including return,
            # break, and exceptions. Keep that cleanup explicit for both backends.
            name, ty = value.var, value.var_decl
            temp = f"closure.exc.{len(func.locals)}"
            func.locals[temp] = ty
            exc = temp + ".pending"
            func.locals[exc] = OptionalT(ClassT(exc_base))
            set_ = ir.CellSet(cell_ref(name, cells[name]), ir.LocalGet(ty, temp, ty))
            body = [ir.Try([set_, *rewrite(value.body)], [], [], [ir.CellClear(cell_ref(name, cells[name]))], exc)]
            return replace(value, var=temp, body=body)
        if isinstance(value, (ir.Expr, ir.Stmt, ir.Handler)):
            return replace(value, **{f.name: rewrite(getattr(value, f.name)) for f in fields(value)})
        if isinstance(value, list):
            return [rewrite(v) for v in value]
        if isinstance(value, tuple):
            return tuple(rewrite(v) for v in value)
        return value

    # The override receiver cast normally lives in the emitter prologue. It has to
    # precede boxing self, and must not write the removed unboxed local afterwards.
    if func.self_cast is not None and "self" in cells:
        ty = cells["self"]
        prefix.append(ir.CellSet(cell_ref("self", ty), ir.LocalGet(ty, "self.in", dict(func.params)["self.in"])))
        func.self_cast = None
        func.nonnull_locals.discard("self")
    func.body = prefix + rewrite(func.body)
    return func
