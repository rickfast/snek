"""Production body checker and owned typed tree IR, written in Snek.

No Python checker fallback; code emission consumes the completed function IR.
The fixed bootstrap bundle supplies these imports when compiling this to Wasm.
"""
from __future__ import annotations
from snek import i32, i64
from bootstrap.parser import Node, missing, scalar, sequence, string, parse_at
from bootstrap.formats import body_format
from bootstrap.scopes import BodyScope, body_scope, scope_walk, add_name
from bootstrap.declarations import DeclType, DeclParam, DeclSignature, DeclField, Declarations, declaration_error, docstring, named, integer_text, floating, float_key, direct_yield


def body_unsupported(feature: str, node: Node) -> None:
    declaration_error("bootstrap body checker does not yet support " + feature, node)


def int_type(ty: DeclType) -> bool:
    return ty.kind in ("i32", "i64")


def float_type(ty: DeclType) -> bool:
    return ty.kind in ("f32", "f64")


def numeric_type(ty: DeclType) -> bool:
    return int_type(ty) or float_type(ty)


def body_type(ty: DeclType, node: Node) -> None:
    if ty.kind in ("i32", "i64", "f32", "f64", "bool", "str", "None", "__Buf", "TracebackType", "HostObject"):
        return
    if ty.kind in ("class", "Stream"):
        return
    if ty.kind in ("optional", "list", "tuple", "dict", "type", "Iterator", "Coroutine", "Callable"):
        for arg in ty.args:
            body_type(arg, node)
        return
    body_unsupported("body type " + ty.display(), node)


def inner_type(ty: DeclType) -> DeclType:
    return ty.args[0] if ty.kind == "optional" else ty


def optional_type(ty: DeclType) -> DeclType:
    return ty if ty.kind == "optional" else DeclType("optional").add(ty)


def refers_self(node: Node) -> bool:
    if named(node, "self"):
        return True
    for child in node.items:
        if refers_self(child):
            return True
    return False


def rename_body(node: Node, names: dict[str, str]) -> Node:
    out = Node(node.kind, node.text, node.line, node.column)
    out.keys = node.keys.copy()
    for index in range(len(node.items)):
        child = node.items[index]
        if ((node.kind == "Name" and node.keys[index] == "id")
                or (node.kind == "ExceptHandler" and node.keys[index] == "name")) and child.text in names:
            out.items.append(string(names[child.text]))
        else:
            out.items.append(rename_body(child, names))
    return out


def super_initializer(node: Node) -> Node:
    if node.kind == "Expr" and node.get("value").kind == "Call":
        call = node.get("value")
        func = call.get("func")
        if func.kind == "Attribute" and func.get("attr").text == "__init__":
            receiver = func.get("value")
            if receiver.kind == "Call" and named(receiver.get("func"), "super"):
                return call
    return missing()


def number_node(node: Node) -> bool:
    while node.kind == "UnaryOp" and node.get("op").kind in ("UAdd", "USub"):
        node = node.get("operand")
    return node.kind == "Constant" and node.get("value").kind == "Number"


def integer_node(node: Node) -> bool:
    while node.kind == "UnaryOp" and node.get("op").kind in ("UAdd", "USub"):
        node = node.get("operand")
    return node.kind == "Constant" and node.get("value").kind == "Number" and not floating(node.get("value").get("text").text)


def none_node(node: Node) -> bool:
    return node.kind == "Constant" and node.get("value").kind == "none"


def numeric_join(a: DeclType, b: DeclType) -> DeclType:
    if not numeric_type(a) or not numeric_type(b):
        return DeclType("unknown")
    if a.same(b):
        return a
    if (a.kind == "i32" and b.kind in ("i64", "f64")) or (a.kind == "f32" and b.kind == "f64"):
        return b
    if (b.kind == "i32" and a.kind in ("i64", "f64")) or (b.kind == "f32" and a.kind == "f64"):
        return a
    return DeclType("unknown")


def bound_names(node: Node, out: list[str]) -> None:
    if node.kind in ("FunctionDef", "AsyncFunctionDef", "ClassDef", "Lambda"):
        if node.kind != "Lambda":
            name = node.get("name").text
            if name not in out:
                out.append(name)
        return
    if node.kind == "ExceptHandler" and node.get("name").kind != "none":
        name = node.get("name").text
        if name not in out:
            out.append(name)
    if node.kind == "withitem" and node.get("optional_vars").kind != "none":
        bind_target(node.get("optional_vars"), out)
    if node.kind in ("Assign", "AnnAssign", "AugAssign", "For"):
        targets: list[Node] = []
        if node.kind == "Assign":
            targets = node.get("targets").items
        else:
            targets = [node.get("target")]
        for target in targets:
            bind_target(target, out)
    for child in node.items:
        bound_names(child, out)


def bind_target(node: Node, out: list[str]) -> None:
    if node.kind == "Name":
        name = node.get("id").text
        if name not in out:
            out.append(name)
    elif node.kind in ("List", "Tuple"):
        for child in node.get("elts").items:
            bind_target(child, out)


def direct_break(node: Node) -> bool:
    if node.kind == "Break":
        return True
    if node.kind in ("While", "For", "FunctionDef", "AsyncFunctionDef", "ClassDef", "Lambda"):
        return False
    for child in node.items:
        if direct_break(child):
            return True
    return False


def handler_names(node: Node, out: list[str]) -> None:
    if node.kind in ("FunctionDef", "AsyncFunctionDef", "ClassDef", "Lambda"):
        return
    if node.kind == "ExceptHandler" and node.get("name").kind != "none":
        name = node.get("name").text
        if name not in out:
            out.append(name)
    for child in node.items:
        handler_names(child, out)


class BodyExpr:
    kind: str
    ty: DeclType
    node: Node
    text: str
    args: list[BodyExpr]
    pre: list[BodyStmt]
    declared: DeclType
    mixed: bool

    def __init__(self, kind: str, ty: DeclType, node: Node, text: str = "") -> None:
        self.kind = kind
        self.ty = ty
        self.node = node
        self.text = text
        self.args = []
        self.pre = []
        self.declared = ty
        self.mixed = False

    def add(self, value: BodyExpr) -> BodyExpr:
        self.args.append(value)
        return self

    def tree(self) -> Node:
        args: list[Node] = []
        pre: list[Node] = []
        for arg in self.args:
            args.append(arg.tree())
        for stmt in self.pre:
            pre.append(stmt.tree())
        return (Node(self.kind, "", self.node.line, self.node.column).put("type", self.ty.tree())
                .put("text", string(self.text)).put("args", sequence(args)).put("pre", sequence(pre))
                .put("declared", self.declared.tree()).put("mixed", Node("scalar", "true" if self.mixed else "false")))


class BodyHandler:
    classes: list[str]
    catches_all: bool
    name: str
    declared: DeclType
    body: list[BodyStmt]

    def __init__(self) -> None:
        self.classes = []
        self.catches_all = False
        self.name = ""
        self.declared = DeclType("None")
        self.body = []

    def tree(self) -> Node:
        classes: list[Node] = []
        for name in self.classes:
            classes.append(string(name))
        return (Node("Handler").put("classes", sequence(classes)).put("name", string(self.name))
                .put("catches_all", Node("scalar", "true" if self.catches_all else "false"))
                .put("declared", self.declared.tree()).put("body", body_trees(self.body)))


class BodyStmt:
    kind: str
    node: Node
    name: str
    exprs: list[BodyExpr]
    body: list[BodyStmt]
    otherwise: list[BodyStmt]
    step: list[BodyStmt]
    sep: str
    end: str
    handlers: list[BodyHandler]
    finalbody: list[BodyStmt]
    has_final: bool

    def __init__(self, kind: str, node: Node, name: str = "") -> None:
        self.kind = kind
        self.node = node
        self.name = name
        self.exprs = []
        self.body = []
        self.otherwise = []
        self.step = []
        self.sep = " "
        self.end = "\n"
        self.handlers = []
        self.finalbody = []
        self.has_final = False

    def add(self, value: BodyExpr) -> BodyStmt:
        self.exprs.append(value)
        return self

    def tree(self) -> Node:
        exprs: list[Node] = []
        handlers: list[Node] = []
        for expr in self.exprs:
            exprs.append(expr.tree())
        for handler in self.handlers:
            handlers.append(handler.tree())
        return (Node(self.kind, "", self.node.line, self.node.column).put("name", string(self.name))
                .put("exprs", sequence(exprs)).put("body", body_trees(self.body))
                .put("otherwise", body_trees(self.otherwise)).put("step", body_trees(self.step))
                .put("sep", string(self.sep)).put("end", string(self.end))
                .put("handlers", sequence(handlers)).put("finalbody", body_trees(self.finalbody))
                .put("has_final", Node("scalar", "true" if self.has_final else "false")))


def body_trees(stmts: list[BodyStmt]) -> Node:
    out: list[Node] = []
    for stmt in stmts:
        out.append(stmt.tree())
    return sequence(out)


class PendingClosure:
    node: Node
    sig: DeclSignature
    value: BodyExpr

    def __init__(self, node: Node, sig: DeclSignature, value: BodyExpr) -> None:
        self.node = node
        self.sig = sig
        self.value = value


def callable_type(sig: DeclSignature) -> DeclType:
    result = DeclType("Callable")
    for param in sig.params:
        result.add(param.ty)
    return result.add(sig.ret)


class BodyFlow:
    assigned: dict[str, bool]
    narrowed: dict[str, DeclType]

    def __init__(self) -> None:
        self.assigned = {}
        self.narrowed = {}

    def copy(self) -> BodyFlow:
        result = BodyFlow()
        result.assigned = self.assigned.copy()
        result.narrowed = self.narrowed.copy()
        return result

    def merge(self, other: BodyFlow) -> BodyFlow:
        result = BodyFlow()
        for name in self.assigned:
            if name in other.assigned:
                result.assigned[name] = True
        for name in self.narrowed:
            if name in other.narrowed and self.narrowed[name].same(other.narrowed[name]):
                result.narrowed[name] = self.narrowed[name]
        return result

    def forget_handlers(self, node: Node) -> None:
        names: list[str] = []
        handler_names(node, names)
        for name in names:
            if name in self.assigned:
                del self.assigned[name]
            if name in self.narrowed:
                del self.narrowed[name]

    def forget_narrowings(self, node: Node) -> None:
        names: list[str] = []
        bound_names(node, names)
        for name in names:
            if name in self.narrowed:
                del self.narrowed[name]


class BodyFunction:
    """Completed function IR consumed by emitters, without checking state."""
    name: str
    sig: DeclSignature
    locals: dict[str, DeclType]
    body: list[BodyStmt]
    owner: str
    method_decl: str
    machine: bool
    closure_type: DeclType

    def __init__(self, name: str, sig: DeclSignature) -> None:
        self.name = name
        self.sig = sig
        self.locals = {}
        self.body = []
        self.owner = ""
        self.method_decl = ""
        self.machine = False
        self.closure_type = DeclType("None")

    def tree(self) -> Node:
        locals: list[Node] = []
        for name in self.locals:
            locals.append(Node("Local").put("name", string(name)).put("type", self.locals[name].tree()))
        return (Node("FunctionIR").put("name", string(self.name)).put("signature", self.sig.tree())
                .put("locals", sequence(locals)).put("body", body_trees(self.body))
                .put("machine", Node("scalar", "true" if self.machine else "false"))
                .put("closure_type", self.closure_type.tree())
                .put("owner", string(self.owner)).put("method_decl", string(self.method_decl)))


class BodyContext:
    """Temporary scope, flow and lowering state while checking one function."""
    function: BodyFunction
    types: dict[str, DeclType]
    bound: list[str]
    flow: BodyFlow
    loop_depth: i32
    temps: i32
    initializing: bool
    handler_excs: list[str]
    finally_loops: list[i32]
    ctor_assigned: dict[str, str]
    inline_exits: list[bool]
    inline_return_ok: bool
    async_body: bool
    result_type: DeclType
    yield_type: DeclType
    scope: BodyScope
    nested_sigs: dict[str, DeclSignature]
    pending: list[PendingClosure]

    def __init__(self, name: str, sig: DeclSignature) -> None:
        self.function = BodyFunction(name, sig)
        self.types = {}
        self.bound = []
        self.flow = BodyFlow()
        self.loop_depth = 0
        self.temps = 0
        self.initializing = False
        self.handler_excs = []
        self.finally_loops = []
        self.ctor_assigned = {}
        self.inline_exits = []
        self.inline_return_ok = False
        self.async_body = False
        self.result_type = sig.ret
        self.yield_type = DeclType("None")
        self.scope = BodyScope(missing())
        self.nested_sigs = {}
        self.pending = []
        for param in sig.params:
            self.types[param.name] = param.ty
            self.bound.append(param.name)
            self.flow.assigned[param.name] = True

    def declare(self, name: str, ty: DeclType, node: Node) -> None:
        if name in self.types:
            if not self.types[name].same(ty):
                declaration_error("'" + name + "' was already declared as " + self.types[name].display(), node)
        else:
            self.function.locals[name] = ty
            self.types[name] = ty

    def get(self, name: str, node: Node) -> BodyExpr:
        if name not in self.types or name not in self.flow.assigned:
            declaration_error("local variable '" + name + "' may be used before it is assigned", node)
        declared = self.types[name]
        captured = name in self.scope.cells or name in self.scope.free
        result = BodyExpr("CapturedGet" if captured else "LocalGet", declared if captured else self.flow.narrowed.get(name, declared), node, name)
        result.declared = declared
        return result

    def temp(self, ty: DeclType, node: Node) -> str:
        name = "b." + str(self.temps)
        self.temps += 1
        self.declare(name, ty, node)
        self.flow.assigned[name] = True
        return name


class BodyBlock:
    stmts: list[BodyStmt]
    terminated: bool

    def __init__(self) -> None:
        self.stmts = []
        self.terminated = False


class BodyChecker:
    decl: Declarations
    ctx: BodyContext
    functions: list[BodyFunction]
    unicode_dynamic: bool
    unicode_extra: list[i32]
    unicode_flags: i32
    unicode_cases: list[i32]
    unicode_decimal: bool
    helpers: dict[str, Node]
    helper_sigs: dict[str, DeclSignature]
    helper_queue: list[str]
    helper_posonly: dict[str, i32]
    intrinsics: dict[str, list[str]]
    builtin_mode: bool
    runtime_abi: str
    nclosures: i32

    def __init__(self, decl: Declarations, runtime: Node, intrinsics: dict[str, list[str]]) -> None:
        self.decl = decl
        self.ctx = BodyContext("", DeclSignature())
        self.functions = []
        self.unicode_dynamic = False
        self.unicode_extra = []
        self.unicode_flags = 1  # NONPRINT: repr always needs printability data.
        self.unicode_cases = []
        self.unicode_decimal = False
        self.helpers = {}
        self.helper_sigs = {}
        self.helper_queue = []
        self.helper_posonly = {}
        self.intrinsics = intrinsics
        self.builtin_mode = False
        self.runtime_abi = runtime.get("runtime_abi").text
        self.nclosures = 0
        for node in runtime.get("body").items:
            if node.kind != "FunctionDef" and node.kind != "FunctionSource":
                declaration_error("runtime source must contain only functions", node)
            self.helpers[node.get("name").text] = node

    def run(self) -> None:
        for name in self.decl.class_order:
            cls = self.decl.classes[name]
            if not cls.builtin:
                for field_name in cls.fields:
                    body_type(cls.fields[field_name].ty, cls.node)
                self.constructor(name)
                for method_name in cls.methods:
                    method = cls.methods[method_name]
                    if method.owner == name:
                        self.function(name + "." + method_name, method.sig, method.node, name, method.decl)
        for name in self.decl.functions:
            sig = self.decl.functions[name]
            node = self.decl.function_nodes[name]
            self.function(name, sig, node, "", "")
        self.ctx = BodyContext("snek.main", DeclSignature())
        main = Node("Module").put("body", sequence(self.decl.main))
        self.prepare_scope(body_scope(main, []), self.ctx)
        self.ctx.function.body = self.block(self.decl.main).stmts
        self.finish_closures()
        self.functions.append(self.ctx.function)
        self.builtin_mode = True
        while len(self.helper_queue) > 0:
            helper = self.helper_queue.pop()
            self.function(helper, self.helper_sigs[helper], self.helpers[helper], "", "")
        self.builtin_mode = False
        # Completed IR owns no scope/flow/closure bookkeeping. Do not retain
        # the final helper's checking context through the returned checker.
        self.ctx = BodyContext("", DeclSignature())

    def annotation(self, node: Node) -> DeclType:
        if not self.builtin_mode:
            return self.decl.resolve_type(node)
        if node.kind == "Name" and node.get("id").text in ("str", "bool", "i32", "i64", "f32", "f64", "__Buf"):
            return DeclType(node.get("id").text)
        if none_node(node):
            return DeclType("None")
        if node.kind == "BinOp" and node.get("op").kind == "BitOr" and none_node(node.get("right")):
            return optional_type(self.annotation(node.get("left")))
        if node.kind == "Subscript" and named(node.get("value"), "list"):
            return DeclType("list").add(self.annotation(node.get("slice")))
        if node.kind == "Subscript" and named(node.get("value"), "tuple"):
            result = DeclType("tuple")
            for item in node.get("slice").get("elts").items:
                result.add(self.annotation(item))
            return result
        declaration_error("unsupported runtime helper annotation", node)
        return DeclType("unknown")

    def helper_signature(self, name: str) -> DeclSignature:
        if name not in self.helper_sigs:
            node = self.helpers[name]
            if node.kind == "FunctionSource":
                functions = parse_at(node.text, node.line).get("body").items
                if len(functions) != 1 or functions[0].kind != "FunctionDef" or functions[0].get("name").text != name:
                    declaration_error("invalid indexed runtime helper " + name, node)
                node = functions[0]
                self.helpers[name] = node
            arguments = node.get("args")
            args = arguments.get("posonlyargs").items.copy()
            args.extend(arguments.get("args").items)
            if (arguments.get("vararg").kind != "none" or arguments.get("kwarg").kind != "none"
                    or len(arguments.get("kwonlyargs").items) != 0):
                declaration_error("unsupported runtime helper signature", node)
            defaults = arguments.get("defaults").items
            offset = len(args) - len(defaults)
            sig = DeclSignature()
            previous = self.builtin_mode
            self.builtin_mode = True
            for index in range(len(args)):
                arg = args[index]
                default = missing() if index < offset else defaults[index - offset]
                ty = self.annotation(arg.get("annotation"))
                if default.kind != "none":
                    self.decl.check_default(default, ty, arg.get("arg").text)
                sig.params.append(DeclParam(arg.get("arg").text, ty, default))
            sig.ret = self.annotation(node.get("returns"))
            self.builtin_mode = previous
            self.helper_sigs[name] = sig
            self.helper_posonly[name] = len(arguments.get("posonlyargs").items)
            self.helper_queue.append(name)
        return self.helper_sigs[name]

    def intrinsic(self, name: str, node: Node) -> BodyExpr:
        spec = self.intrinsics[name]
        self.arity(node, len(spec) - 2, len(spec) - 2)
        if len(node.get("keywords").items) != 0:
            declaration_error("runtime intrinsics take positional arguments only", node)
        result = BodyExpr("Call", DeclType(spec[1]), node, spec[0])
        args = node.get("args").items
        for index in range(len(args)):
            ty = DeclType(spec[index + 2])
            result.add(self.coerce(self.expr(args[index], ty), ty, args[index]))
        if name in ("__has", "__all_flags", "__any_flags"):
            mask = result.args[1]
            self.unicode_flags |= i32(mask.text) if mask.kind == "Const" else 4095
        elif name == "__putcase":
            case = result.args[2]
            if case.kind == "Const":
                kind = i32(case.text)
                if kind not in self.unicode_cases:
                    self.unicode_cases.append(kind)
            else:
                self.unicode_cases = [0, 1, 2, 3]
        elif name == "__decimal":
            self.unicode_decimal = True
        return result

    def function(self, name: str, sig: DeclSignature, node: Node, owner: str, method_decl: str) -> None:
        if sig.host != "":
            self.functions.append(BodyFunction(name, sig))
            return
        body_type(sig.ret, node)
        for param in sig.params:
            body_type(param.ty, node)
        self.ctx = BodyContext(name, sig)
        if node.kind == "AsyncFunctionDef":
            self.ctx.async_body = True
            self.ctx.function.machine = True
            self.ctx.result_type = sig.ret.args[0]
        elif direct_yield(node.get("body")):
            self.ctx.function.machine = True
            self.ctx.result_type = DeclType("None")
            self.ctx.yield_type = sig.ret.args[0]
        self.ctx.function.owner = owner
        self.ctx.function.method_decl = method_decl
        if owner != "":
            self.ctx.types["self"] = DeclType("class", owner)
            self.ctx.flow.assigned["self"] = True
            self.ctx.bound.append("self")
        body = node.get("body")
        self.prepare_scope(body_scope(node, []), self.ctx)
        block = self.block(body.items)
        if self.ctx.result_type.kind != "None" and not block.terminated:
            declaration_error("'" + name + "' can reach the end without returning a " + self.ctx.result_type.display(), node)
        self.ctx.function.body = block.stmts
        self.finish_closures()
        self.functions.append(self.ctx.function)

    def prepare_scope(self, scope: BodyScope, parent: BodyContext) -> None:
        self.ctx.scope = scope
        self.ctx.bound = scope.bound + scope.free
        for name in scope.free:
            if name not in parent.types:
                declaration_error("cannot infer captured variable '" + name + "'; add a local type annotation", scope.node)
            self.ctx.types[name] = parent.types[name]
            self.ctx.flow.assigned[name] = True
        self.prepare(scope.node.get("body"))
        nodes: list[Node] = []
        scope_walk(scope.node.get("body"), nodes)
        for node in nodes:
            if node.kind in ("FunctionDef", "AsyncFunctionDef"):
                sig = self.decl.signature(node, False)
                name = node.get("name").text
                self.ctx.nested_sigs[str(node.line) + ":" + str(node.column)] = sig
                self.ctx.declare(name, callable_type(sig), node)

    def lambda_signature(self, node: Node, params: list[DeclType]) -> DeclSignature:
        args = node.get("args")
        if (len(args.get("defaults").items) != 0 or len(args.get("kwonlyargs").items) != 0
                or len(args.get("posonlyargs").items) != 0 or args.get("vararg").kind != "none" or args.get("kwarg").kind != "none"):
            declaration_error("lambda defaults, *args, **kwargs and keyword-only/positional-only parameters are not supported", node)
        if len(args.get("args").items) != len(params):
            declaration_error("lambda parameter count does not match its Callable type", node)
        sig = DeclSignature()
        for index in range(len(params)):
            sig.params.append(DeclParam(args.get("args").items[index].get("arg").text, params[index], missing()))
        return sig

    def defer_closure(self, node: Node, sig: DeclSignature) -> BodyExpr:
        if self.ctx.initializing:
            declaration_error("closures in __init__ are not supported yet; use an ordinary method", node)
        scope = self.ctx.scope.child(node)
        if self.ctx.function.name == "snek.main" and len(scope.free) > 0:
            declaration_error("module state cannot be captured; put the enclosing code in a function", node)
        name = "closure." + str(self.nclosures)
        self.nclosures += 1
        value = BodyExpr("Closure", callable_type(sig), node, name)
        self.ctx.pending.append(PendingClosure(node, sig, value))
        return value

    def call_value(self, node: Node, value: BodyExpr) -> BodyExpr:
        if value.ty.kind != "Callable":
            declaration_error(value.ty.display() + " is not callable (narrow optional callables first)", node)
        if len(node.get("keywords").items) != 0:
            declaration_error("Callable values take positional arguments only", node)
        args = node.get("args").items
        if len(args) != len(value.ty.args) - 1:
            declaration_error("Callable argument count does not match its type", node)
        result = BodyExpr("CallValue", value.ty.args[-1], node).add(value)
        for index in range(len(args)):
            if args[index].kind == "Starred":
                declaration_error("Callable values take positional arguments only (no *args)", args[index])
            ty = value.ty.args[index]
            result.add(self.coerce(self.expr(args[index], ty), ty, args[index]))
        return result

    def finish_closures(self) -> None:
        parent = self.ctx
        for pending in parent.pending:
            self.finish_pending(pending, parent)
        self.lower_cells()

    def finish_pending(self, pending: PendingClosure, parent: BodyContext) -> None:
        node = pending.node
        sig = pending.sig
        self.ctx = BodyContext(pending.value.text, sig)
        self.ctx.function.closure_type = callable_type(sig)
        self.prepare_scope(parent.scope.child(node), parent)
        if node.kind == "Lambda":
            expr = node.get("body")
            if (sig.ret.kind == "None" and expr.kind == "Call" and named(expr.get("func"), "print")
                    and "print" not in self.ctx.types and "print" not in self.ctx.bound):
                self.ctx.function.body = self.print_statement(expr).stmts + [BodyStmt("Return", node)]
            else:
                value = self.expr(expr, sig.ret)
                if sig.ret.kind == "unknown":
                    self.no_mixed(value, expr)
                    sig.ret = value.ty
                    self.ctx.result_type = value.ty
                    self.ctx.function.closure_type = callable_type(sig)
                    pending.value.ty = self.ctx.function.closure_type
                value = self.coerce(value, sig.ret, expr)
                if sig.ret.kind == "None":
                    if value.kind != "NoneLit":
                        self.ctx.function.body.append(BodyStmt("ExprStmt", expr).add(value))
                    self.ctx.function.body.append(BodyStmt("Return", node))
                else:
                    self.ctx.function.body = [BodyStmt("Return", node).add(value)]
        else:
            if node.kind == "AsyncFunctionDef":
                self.ctx.async_body = True
                self.ctx.function.machine = True
                self.ctx.result_type = sig.ret.args[0]
            elif direct_yield(node.get("body")):
                self.ctx.function.machine = True
                self.ctx.result_type = DeclType("None")
                self.ctx.yield_type = sig.ret.args[0]
            checked = self.block(node.get("body").items)
            if self.ctx.result_type.kind != "None" and not checked.terminated:
                declaration_error("nested function can reach the end without returning a " + self.ctx.result_type.display(), node)
            self.ctx.function.body = checked.stmts
        self.finish_closures()
        self.functions.append(self.ctx.function)
        for name in sorted(self.ctx.scope.free):
            pending.value.add(self.cell_ref(name, parent.types[name], node))
        self.ctx = parent

    def key_lambda(self, node: Node, elem: DeclType) -> BodyExpr:
        sig = self.lambda_signature(node, [elem])
        sig.ret = DeclType("unknown")
        value = self.defer_closure(node, sig)
        pending = self.ctx.pending.pop()
        self.finish_pending(pending, self.ctx)
        return value

    def cell_ref(self, name: str, ty: DeclType, node: Node) -> BodyExpr:
        cell = DeclType("Cell").add(ty)
        value = BodyExpr("LocalGet", cell, node, "cell." + name)
        value.declared = cell
        return value

    def cell_expression(self, expr: BodyExpr, cells: dict[str, DeclType]) -> BodyExpr:
        if expr.kind in ("LocalGet", "CapturedGet") and expr.text in cells:
            value = BodyExpr("FreeCellGet" if expr.text in self.ctx.scope.free else "CellGet", expr.ty, expr.node, expr.text)
            return value.add(self.cell_ref(expr.text, cells[expr.text], expr.node))
        args: list[BodyExpr] = []
        for arg in expr.args:
            args.append(self.cell_expression(arg, cells))
        expr.args = args
        expr.pre = self.cell_statements(expr.pre, cells)
        return expr

    def cell_statements(self, stmts: list[BodyStmt], cells: dict[str, DeclType]) -> list[BodyStmt]:
        result: list[BodyStmt] = []
        for stmt in stmts:
            args: list[BodyExpr] = []
            for expr in stmt.exprs:
                args.append(self.cell_expression(expr, cells))
            stmt.exprs = args
            stmt.body = self.cell_statements(stmt.body, cells)
            stmt.otherwise = self.cell_statements(stmt.otherwise, cells)
            stmt.step = self.cell_statements(stmt.step, cells)
            stmt.finalbody = self.cell_statements(stmt.finalbody, cells)
            for handler in stmt.handlers:
                handler.body = self.cell_statements(handler.body, cells)
                if handler.name in cells:
                    name = handler.name
                    temporary = self.ctx.temp(handler.declared, stmt.node)
                    caught = self.ctx.get(temporary, stmt.node)
                    cell = self.cell_ref(name, cells[name], stmt.node)
                    cleanup = BodyStmt("Try", stmt.node, self.ctx.temp(optional_type(DeclType("class", "BaseException")), stmt.node))
                    cleanup.body = [BodyStmt("CellSet", stmt.node).add(cell).add(caught)] + handler.body
                    cleanup.finalbody = [BodyStmt("CellClear", stmt.node).add(cell)]
                    cleanup.has_final = True
                    handler.name = temporary
                    handler.body = [cleanup]
            if stmt.kind == "SetLocal" and stmt.name in cells:
                result.append(BodyStmt("CellSet", stmt.node).add(self.cell_ref(stmt.name, cells[stmt.name], stmt.node)).add(stmt.exprs[0]))
            else:
                result.append(stmt)
        return result

    def lower_cells(self) -> None:
        cells: dict[str, DeclType] = {}
        for name in self.ctx.scope.cells + self.ctx.scope.free:
            if name in self.ctx.types:
                cells[name] = self.ctx.types[name]
        if len(cells) == 0:
            return
        node = self.ctx.scope.node
        free = sorted(self.ctx.scope.free)
        layout = DeclType("Env", "layout")
        for name in free:
            layout.add(DeclType("Cell").add(self.ctx.types[name]))
        params: list[str] = []
        for param in self.ctx.function.sig.params:
            params.append(param.name)
        if self.ctx.function.owner != "":
            params.append("self")
        prefix: list[BodyStmt] = []
        for name in sorted(list(cells)):
            ty = cells[name]
            ref = self.cell_ref(name, ty, node)
            self.ctx.function.locals[ref.text] = ref.ty
            if name in free:
                env = BodyExpr("LocalGet", DeclType("Env"), node, "closure.env")
                env.declared = env.ty
                value = BodyExpr("EnvGet", ref.ty, node, str(free.index(name))).add(env)
                value.declared = layout
                prefix.append(BodyStmt("SetLocal", node, ref.text).add(value))
            else:
                prefix.append(BodyStmt("SetLocal", node, ref.text).add(BodyExpr("CellNew", ref.ty, node)))
                if name in params:
                    value = BodyExpr("LocalGet", ty, node, name)
                    value.declared = ty
                    if name == "self" and self.ctx.function.owner != self.ctx.function.method_decl:
                        value.text = "self.in"
                        value.declared = DeclType("class", self.ctx.function.method_decl)
                    prefix.append(BodyStmt("CellSet", node).add(ref).add(value))
            if name not in params and name in self.ctx.function.locals:
                del self.ctx.function.locals[name]
        self.ctx.function.body = prefix + self.cell_statements(self.ctx.function.body, cells)

    def subclass(self, child: str, parent: str) -> bool:
        while child != "":
            if child == parent:
                return True
            child = self.decl.classes[child].base
        return False

    def assignable(self, source: DeclType, target: DeclType) -> bool:
        if source.same(target):
            return True
        if target.kind == "optional":
            return source.kind == "None" or self.assignable(inner_type(source), target.args[0])
        if source.kind == "type" and target.kind == "type":
            return self.assignable(source.args[0], target.args[0])
        return source.kind == "class" and target.kind == "class" and self.subclass(source.name, target.name)

    def join(self, left: DeclType, right: DeclType) -> DeclType:
        if left.same(right):
            return left
        if left.kind == "None" and right.reference():
            return optional_type(right)
        if right.kind == "None" and left.reference():
            return optional_type(left)
        a = inner_type(left)
        b = inner_type(right)
        result = DeclType("unknown")
        if a.same(b):
            result = a
        elif a.kind == "class" and b.kind == "class":
            name = a.name
            while name != "":
                if self.subclass(b.name, name):
                    result = DeclType("class", name)
                    break
                name = self.decl.classes[name].base
        elif a.kind == "type" and b.kind == "type":
            common = self.join(a.args[0], b.args[0])
            if common.kind == "class":
                result = DeclType("type").add(common)
        if result.kind != "unknown":
            return optional_type(result) if left.kind == "optional" or right.kind == "optional" else result
        return numeric_join(left, right)

    def prepare(self, node: Node) -> None:
        # Explicit annotations apply throughout the lexical scope, including
        # earlier assignments. They do not make a value definitely assigned.
        if node.kind in ("FunctionDef", "AsyncFunctionDef", "ClassDef", "Lambda"):
            return
        if node.kind == "AnnAssign" and node.get("target").kind == "Name":
            if node.get("target").get("id").text in self.ctx.scope.free:
                declaration_error("a nonlocal variable cannot be annotated", node)
            ty = self.annotation(node.get("annotation"))
            body_type(ty, node)
            if ty.kind == "None":
                declaration_error("a variable cannot have type None", node)
            self.ctx.declare(node.get("target").get("id").text, ty, node)
        for child in node.items:
            self.prepare(child)

    def tree(self) -> Node:
        functions: list[Node] = []
        for function in self.functions:
            functions.append(function.tree())
        cases: list[Node] = []
        for case in self.unicode_cases:
            cases.append(scalar(case))
        return (Node("BodyModule").put("schema", scalar(7)).put("subset", string("language-v7"))
                .put("runtime_abi", string(self.runtime_abi))
                .put("body_checking", string("complete-for-subset"))
                .put("declarations", self.decl.tree()).put("functions", sequence(functions))
                .put("unicode_dynamic", Node("scalar", "true" if self.unicode_dynamic else "false"))
                .put("unicode_extra", self.unicode_points()).put("unicode_flags", scalar(self.unicode_flags))
                .put("unicode_cases", sequence(cases))
                .put("unicode_decimal", Node("scalar", "true" if self.unicode_decimal else "false")))

    def unicode_points(self) -> Node:
        points: list[Node] = []
        for point in self.unicode_extra:
            points.append(scalar(point))
        return sequence(points)

    def no_mixed(self, value: BodyExpr, node: Node) -> None:
        if value.mixed:
            declaration_error("value can be an int at run time; make both conditional branches floats or convert explicitly", node)

    def coerce(self, value: BodyExpr, target: DeclType, node: Node, arithmetic: bool = False) -> BodyExpr:
        if not arithmetic:
            self.no_mixed(value, node)
            if float_type(target) and integer_node(node):
                declaration_error("expected " + target.display() + "; use a float literal or explicit conversion, not an integer literal", node)
        if value.ty.same(target):
            return value
        if target.kind == "optional":
            if value.ty.kind == "None":
                if value.kind != "NoneLit":
                    return self.sequence_expr([BodyStmt("ExprStmt", node).add(value)], BodyExpr("NoneLit", target, node))
                return BodyExpr("NoneLit", target, node)
        if self.assignable(value.ty, target):
            return value
        joined = numeric_join(value.ty, target)
        if joined.same(target):
            if int_type(value.ty) and float_type(target) and not arithmetic:
                declaration_error("ints only mix into floats inside arithmetic; convert explicitly with " + target.kind + "(...)" , node)
            return BodyExpr("Convert", target, node).add(value)
        declaration_error("expected " + target.display() + ", got " + value.ty.display(), node)
        return value

    def number(self, node: Node, expected: DeclType) -> BodyExpr:
        raw = node
        negative = False
        while raw.kind == "UnaryOp":
            if raw.get("op").kind == "USub":
                negative = not negative
            raw = raw.get("operand")
        text = raw.get("value").get("text").text
        if floating(text):
            bits_text = ""
            try:
                bits_text = float_key(text)
            except ValueError as error:
                declaration_error(str(error), node)
            parts = bits_text.split(",")
            if negative:
                parts[-1] = str(i32(parts[-1]) | 128)
            ty = DeclType("f32" if expected.kind == "f32" else "f64")
            return BodyExpr("Const", ty, node, "f64:" + ",".join(parts))
        bits = 32 if expected.kind == "i32" else 64
        value = integer_text(text, negative, bits, node)
        ty = DeclType("i64")
        if expected.kind != "i64" and i64(value) >= -2147483648 and i64(value) <= 2147483647:
            ty = DeclType("i32")
        expr = BodyExpr("Const", ty, node, value)
        if float_type(expected):
            # Contextual literals may mix into arithmetic. Stores and calls
            # still see the original syntax in coerce() and reject int->float.
            return BodyExpr("Convert", expected, node).add(expr)
        return expr

    def condition(self, node: Node) -> BodyExpr:
        value = self.expr(node, DeclType("bool"))
        if value.ty.kind != "bool":
            declaration_error("condition must be bool, got " + value.ty.display() + " (no implicit truthiness)", node)
        return value

    def facts(self, node: Node, truth: bool, out: dict[str, DeclType]) -> None:
        if node.kind == "UnaryOp" and node.get("op").kind == "Not":
            self.facts(node.get("operand"), not truth, out)
        elif node.kind == "BoolOp":
            op = node.get("op").kind
            if (truth and op == "And") or (not truth and op == "Or"):
                for child in node.get("values").items:
                    self.facts(child, truth, out)
        elif node.kind == "Compare" and len(node.get("ops").items) == 1:
            op = node.get("ops").items[0].kind
            left = node.get("left")
            right = node.get("comparators").items[0]
            if none_node(right) and left.kind == "Name" and ((op == "IsNot" and truth) or (op == "Is" and not truth)):
                name = left.get("id").text
                if name in self.ctx.types and self.ctx.types[name].kind == "optional":
                    out[name] = self.ctx.types[name].args[0]
        elif truth and node.kind == "Call" and named(node.get("func"), "isinstance") and "isinstance" not in self.ctx.bound:
            args = node.get("args").items
            if len(args) == 2 and args[0].kind == "Name" and args[1].kind == "Name":
                name = args[0].get("id").text
                cls = args[1].get("id").text
                if name in self.ctx.types and cls in self.decl.classes:
                    old = inner_type(self.ctx.types[name])
                    if old.kind == "class" and self.subclass(cls, old.name):
                        out[name] = DeclType("class", cls)

    def expr(self, node: Node, expected: DeclType) -> BodyExpr:
        path = self.library_path(node)
        if path == ["sys", "argv"]:
            return BodyExpr("Call", DeclType("list").add(DeclType("str")), node, "snek.lib.argv")
        if len(path) == 2 and path[0] == "sys" and path[1] in ("stdin", "stdout", "stderr"):
            ty = DeclType("Stream", "TextInput" if path[1] == "stdin" else "TextOutput")
            return BodyExpr("Call", ty, node, "snek.sys.get_" + path[1])
        if node.kind == "Await":
            if not self.ctx.async_body:
                declaration_error("await is only allowed inside async def", node)
            awaited = self.expr(node.get("value"), DeclType("unknown"))
            if awaited.ty.kind != "Coroutine":
                declaration_error("await needs a coroutine", node)
            return BodyExpr("Await", awaited.ty.args[0], node).add(awaited)
        if number_node(node):
            return self.number(node, expected)
        if node.kind == "Constant":
            value = node.get("value")
            if value.kind == "str":
                return BodyExpr("StrLit", DeclType("str"), node, value.text)
            if value.kind == "scalar":
                return BodyExpr("Const", DeclType("bool"), node, value.text)
            if value.kind == "none":
                return BodyExpr("NoneLit", expected if expected.kind == "optional" else DeclType("None"), node)
        if node.kind == "Name":
            name = node.get("id").text
            if name in self.ctx.bound or name in self.ctx.types:
                return self.ctx.get(name, node)
            if name in self.decl.classes:
                return BodyExpr("ClassRef", DeclType("type").add(DeclType("class", name)), node, name)
            if name in self.decl.functions:
                return BodyExpr("FunctionRef", callable_type(self.decl.functions[name]), node, name)
            if name in self.decl.library_imports:
                body_unsupported("standard-library values", node)
            declaration_error("undefined name '" + name + "'", node)
        if node.kind == "UnaryOp":
            op = node.get("op").kind
            if op == "Not":
                return BodyExpr("Unary", DeclType("bool"), node, "not").add(self.condition(node.get("operand")))
            arg = self.expr(node.get("operand"), expected if op != "Invert" or int_type(expected) else DeclType("unknown"))
            if not numeric_type(arg.ty) or (op == "Invert" and not int_type(arg.ty)):
                declaration_error("invalid unary operator on " + arg.ty.display(), node)
            if op == "UAdd":
                return arg
            result = BodyExpr("Unary", arg.ty, node, "invert" if op == "Invert" else "neg").add(arg)
            result.mixed = arg.mixed
            return result
        if node.kind == "BinOp":
            operands = self.operands(node.get("left"), node.get("right"), expected)
            return self.binary(node.get("op").kind, operands[0], operands[1], node)
        if node.kind == "Compare":
            return self.compare(node)
        if node.kind == "BoolOp":
            op = node.get("op").kind
            saved = self.ctx.flow.narrowed.copy()
            values = node.get("values").items
            result = self.condition(values[0])
            self.facts(values[0], op == "And", self.ctx.flow.narrowed)
            for index in range(1, len(values)):
                result = BodyExpr("Logical", DeclType("bool"), node, "and" if op == "And" else "or").add(result).add(self.condition(values[index]))
                self.facts(values[index], op == "And", self.ctx.flow.narrowed)
            self.ctx.flow.narrowed = saved
            return result
        if node.kind == "IfExp":
            return self.conditional(node, expected)
        if node.kind == "Call":
            return self.call(node, expected)
        if node.kind == "JoinedStr":
            return self.fstring(node)
        if node.kind == "Lambda":
            hint = inner_type(expected)
            if hint.kind != "Callable":
                declaration_error("lambda needs a Callable[[...], T] context", node)
            sig = self.lambda_signature(node, hint.args[:-1])
            sig.ret = hint.args[-1]
            return self.defer_closure(node, sig)
        if node.kind in ("List", "Tuple", "Dict"):
            return self.collection(node, inner_type(expected))
        if node.kind == "Attribute":
            return self.attribute(node)
        if node.kind == "Subscript":
            obj = self.expr(node.get("value"), DeclType("unknown"))
            return self.subscript(obj, node.get("slice"), node)
        body_unsupported("expression " + node.kind, node)
        return BodyExpr("NoneLit", DeclType("None"), node)

    def attribute(self, node: Node) -> BodyExpr:
        receiver = node.get("value")
        if (node.get("attr").text == "__name__" and receiver.kind == "Call"
                and named(receiver.get("func"), "type") and "type" not in self.ctx.bound):
            if len(receiver.get("args").items) != 1 or len(receiver.get("keywords").items) != 0:
                declaration_error("type() takes exactly one argument", receiver)
            obj = self.expr(receiver.get("args").items[0], DeclType("unknown"))
            self.no_mixed(obj, receiver)
            if obj.ty.kind == "class":
                return BodyExpr("TypeName", DeclType("str"), node, obj.ty.name).add(obj)
            names = {"i32": "int", "i64": "int", "f32": "float", "f64": "float", "bool": "bool",
                     "str": "str", "list": "list", "tuple": "tuple", "dict": "dict"}
            if obj.ty.kind not in names:
                declaration_error("type(x).__name__ of " + obj.ty.display() + " is not supported (narrow it first)", node)
            return self.sequence_expr([BodyStmt("ExprStmt", node).add(obj)], BodyExpr("StrLit", DeclType("str"), node, names[obj.ty.kind]))
        obj = self.expr(receiver, DeclType("unknown"))
        if obj.ty.kind == "Stream":
            attr = node.get("attr").text
            if attr == "closed":
                return BodyExpr("Call", DeclType("bool"), node, "snek.sys.closed").add(obj)
            if attr == "buffer" and obj.ty.name.startswith("Text"):
                return BodyExpr("Call", DeclType("Stream", obj.ty.name.replace("Text", "Binary")), node, "snek.sys.buffer").add(obj)
            declaration_error(obj.ty.display() + " has no attribute '" + attr + "'", node)
        if obj.ty.kind == "type" and node.get("attr").text == "__name__":
            return BodyExpr("ClassName", DeclType("str"), node).add(obj)
        if inner_type(obj.ty).kind == "TracebackType":
            declaration_error("tracebacks are opaque; only None checks are supported", node)
        field = self.field(obj, node.get("attr").text, node)
        return BodyExpr("GetField", field.ty, node, field.name).add(obj)

    def operands(self, left: Node, right: Node, expected: DeclType) -> list[BodyExpr]:
        hint = expected if int_type(expected) else DeclType("unknown")
        empty_left = (left.kind == "List" and len(left.get("elts").items) == 0) or (left.kind == "Dict" and len(left.get("keys").items) == 0)
        if (number_node(left) and not number_node(right)) or empty_left:
            r = self.expr(right, hint)
            l = self.expr(left, r.ty if empty_left or numeric_type(r.ty) else DeclType("unknown"))
            return [l, r]
        l = self.expr(left, hint)
        collection_hint = (right.kind == "List" and inner_type(l.ty).kind == "list") or (right.kind == "Dict" and inner_type(l.ty).kind == "dict")
        r = self.expr(right, l.ty if collection_hint or (number_node(right) and numeric_type(l.ty)) else DeclType("unknown"))
        return [l, r]

    def binary(self, op: str, left: BodyExpr, right: BodyExpr, node: Node) -> BodyExpr:
        if op == "Add" and left.ty.kind == "tuple" and right.ty.kind == "tuple":
            pre: list[BodyStmt] = []
            a = self.capture(left, pre)
            b = self.capture(right, pre)
            result = BodyExpr("TupleLit", DeclType("tuple"), node)
            for seq in [a, b]:
                for index in range(len(seq.ty.args)):
                    result.ty.add(seq.ty.args[index])
                    result.add(BodyExpr("TupleGet", seq.ty.args[index], node, str(index)).add(seq))
            return self.sequence_expr(pre, result)
        if op == "Mult" and (left.ty.kind == "tuple" or right.ty.kind == "tuple"):
            seq = left if left.ty.kind == "tuple" else right
            count = right if left.ty.kind == "tuple" else left
            if count.kind != "Const" or not int_type(count.ty):
                declaration_error("tuple repetition needs an integer literal (the result has fixed length)", node)
            n = max(i64(count.text), i64(0)) if len(seq.ty.args) > 0 else i64(0)
            if n > 10000 or n * len(seq.ty.args) > 10000:
                declaration_error("tuple repetition would create more than 10000 fields", node)
            pre = []
            seq = self.capture(seq, pre)
            result = BodyExpr("TupleLit", DeclType("tuple"), node)
            for repeat in range(i32(n)):
                for index in range(len(seq.ty.args)):
                    result.ty.add(seq.ty.args[index])
                    result.add(BodyExpr("TupleGet", seq.ty.args[index], node, str(index)).add(seq))
            return self.sequence_expr(pre, result)
        if op == "Add" and left.ty.kind == "str" and right.ty.kind == "str":
            return BodyExpr("StrConcat", DeclType("str"), node).add(left).add(right)
        if op == "Add" and left.ty.kind == "list" and left.ty.same(right.ty):
            return BodyExpr("ListOp", left.ty, node, "concat").add(left).add(right)
        if op == "Mult" and (left.ty.kind in ("list", "str") or right.ty.kind in ("list", "str")):
            seq = left if left.ty.kind in ("list", "str") else right
            count = right if left.ty.kind in ("list", "str") else left
            if count.ty.kind != "i32":
                declaration_error("sequence repetition needs an i32", node)
            pre: list[BodyStmt] = []
            if seq is right:
                count = self.capture(count, pre)
            expr = BodyExpr("Call" if seq.ty.kind == "str" else "ListOp", seq.ty, node,
                            "rt.str_repeat" if seq.ty.kind == "str" else "repeat").add(seq).add(count)
            return self.sequence_expr(pre, expr)
        ops: dict[str, str] = {"Add": "add", "Sub": "sub", "Mult": "mul", "Div": "div", "FloorDiv": "floordiv",
                               "Mod": "mod", "BitAnd": "bitand", "BitOr": "bitor", "BitXor": "bitxor", "LShift": "shl", "RShift": "shr"}
        if op not in ops:
            body_unsupported("operator " + op, node)
        ty = numeric_join(left.ty, right.ty)
        if ty.kind == "unknown":
            declaration_error("incompatible operands " + left.ty.display() + " and " + right.ty.display(), node)
        name = ops[op]
        if op in ("BitAnd", "BitOr", "BitXor", "LShift", "RShift") and not int_type(ty):
            declaration_error("bitwise operators need integer operands", node)
        if op == "Div" and int_type(ty):
            if ty.kind == "i64":
                declaration_error("/ on i64 would lose precision as f64; use // or convert explicitly", node)
            ty = DeclType("f64")
            name = "div_int"
        return BodyExpr("Binary", ty, node, name).add(self.coerce(left, ty, node, True)).add(self.coerce(right, ty, node, True))

    def compare(self, node: Node) -> BodyExpr:
        if len(node.get("ops").items) != 1:
            declaration_error("chained comparisons are not supported yet", node)
        op = node.get("ops").items[0].kind
        left = node.get("left")
        right = node.get("comparators").items[0]
        if op in ("Is", "IsNot"):
            if none_node(left):
                left = right
                right = node.get("left")
            if not none_node(right):
                a = self.expr(left, DeclType("unknown"))
                b = self.expr(right, DeclType("unknown"))
                if (inner_type(a.ty).kind == "HostObject" or inner_type(b.ty).kind == "HostObject") and inner_type(a.ty).kind != inner_type(b.ty).kind:
                    declaration_error("HostObject identity requires two HostObject operands", node)
                if inner_type(a.ty).kind == "TracebackType" or inner_type(b.ty).kind == "TracebackType":
                    declaration_error("tracebacks are opaque; only None checks are supported", node)
                if not a.ty.reference() or not b.ty.reference():
                    declaration_error("identity comparison requires references", node)
                return BodyExpr("Compare", DeclType("bool"), node, "isnot" if op == "IsNot" else "is").add(a).add(b)
            value = self.expr(left, DeclType("unknown"))
            if value.ty.kind != "optional" and not (value.kind == "LocalGet" and value.declared.kind == "optional"):
                declaration_error("this is never None (its type is " + value.ty.display() + ")", left)
            return BodyExpr("IsNull", DeclType("bool"), node, "not" if op == "IsNot" else "").add(value)
        if op in ("In", "NotIn"):
            value = self.contains(left, right, node)
            return BodyExpr("Unary", DeclType("bool"), node, "not").add(value) if op == "NotIn" else value
        names: dict[str, str] = {"Eq": "eq", "NotEq": "ne", "Lt": "lt", "LtE": "le", "Gt": "gt", "GtE": "ge"}
        if op not in names:
            body_unsupported("comparison " + op, node)
        if right.kind == "Tuple" and left.kind != "Tuple":
            value = self.expr(left, DeclType("unknown"))
            hint = inner_type(value.ty)
            if hint.kind != "tuple" or len(hint.args) != len(right.get("elts").items):
                hint = DeclType("unknown")
            operands = [value, self.expr(right, hint)]
        else:
            operands = self.operands(left, right, DeclType("unknown"))
        a = operands[0]
        b = operands[1]
        if inner_type(a.ty).kind in ("type", "Callable", "Stream", "HostObject") and inner_type(b.ty).kind == inner_type(a.ty).kind and op in ("Eq", "NotEq"):
            return BodyExpr("Compare", DeclType("bool"), node, "is" if op == "Eq" else "isnot").add(a).add(b)
        if inner_type(a.ty).kind in ("list", "tuple", "dict", "class") or inner_type(b.ty).kind == "class":
            if op not in ("Eq", "NotEq"):
                declaration_error("ordering comparisons on collections/objects are not supported", node)
            base = inner_type(a.ty)
            other = inner_type(b.ty)
            if base.kind == "None" and other.kind == "class":
                a = self.coerce(a, optional_type(other), left)
                base = other
            if other.kind == "None" and base.kind == "class":
                b = self.coerce(b, optional_type(base), right)
                other = base
            if base.kind == "class" and other.kind == "class":
                self.equality(a.ty, b.ty, node)
                eq = BodyExpr("ObjectEq", DeclType("bool"), node).add(a).add(b)
            else:
                if not base.same(other):
                    declaration_error("cannot compare " + a.ty.display() + " with " + b.ty.display(), node)
                self.equality(base, base, node)
                kind = "ListOp" if base.kind == "list" else "TupleOp" if base.kind == "tuple" else "DictOp"
                eq = BodyExpr(kind, DeclType("bool"), node, "eq").add(a).add(b)
            return BodyExpr("Unary", DeclType("bool"), node, "not").add(eq) if op == "NotEq" else eq
        ty = numeric_join(a.ty, b.ty)
        if a.ty.same(b.ty) and (a.ty.kind == "str" or (a.ty.kind == "bool" and op in ("Eq", "NotEq"))):
            ty = a.ty
        if ty.kind == "unknown":
            declaration_error("cannot compare " + a.ty.display() + " with " + b.ty.display(), node)
        return BodyExpr("Compare", DeclType("bool"), node, names[op]).add(self.coerce(a, ty, left, True)).add(self.coerce(b, ty, right, True))

    def conditional(self, node: Node, expected: DeclType) -> BodyExpr:
        cond = self.condition(node.get("test"))
        hint = DeclType("unknown") if float_type(expected) else expected
        saved = self.ctx.flow.narrowed.copy()
        self.facts(node.get("test"), True, self.ctx.flow.narrowed)
        left = self.expr(node.get("body"), hint)
        self.ctx.flow.narrowed = saved.copy()
        self.facts(node.get("test"), False, self.ctx.flow.narrowed)
        right = self.expr(node.get("orelse"), hint)
        self.ctx.flow.narrowed = saved
        ty = self.join(left.ty, right.ty)
        if ty.kind == "unknown":
            declaration_error("conditional branches have incompatible types", node)
        if ty.kind == "None":
            body_unsupported("None-valued conditional expressions", node)
        mixed = float_type(ty) and (int_type(left.ty) or int_type(right.ty))
        result = BodyExpr("IfExpr", ty, node).add(cond)
        result.add(self.coerce(left, ty, node.get("body"), mixed)).add(self.coerce(right, ty, node.get("orelse"), mixed))
        result.mixed = mixed or left.mixed or right.mixed
        return result

    def as_string(self, value: BodyExpr, node: Node) -> BodyExpr:
        self.no_mixed(value, node)
        if value.ty.kind == "str":
            return value
        if value.ty.kind == "None":
            result = BodyExpr("StrLit", DeclType("str"), node, "None")
            if value.kind != "NoneLit":
                sequence_expr = BodyExpr("Seq", result.ty, node).add(result)
                sequence_expr.pre.append(BodyStmt("ExprStmt", node).add(value))
                return sequence_expr
            return result
        inner = inner_type(value.ty)
        if inner.kind == "class":
            if "__str__" not in self.decl.classes[inner.name].methods and not self.subclass(inner.name, "BaseException"):
                declaration_error("converting " + value.ty.display() + " to str requires __str__", node)
        elif not self.has_repr(value.ty):
            declaration_error("converting " + value.ty.display() + " to str is not supported", node)
        return BodyExpr("ToStr", DeclType("str"), node).add(value)

    def repr_value(self, value: BodyExpr, node: Node) -> BodyExpr:
        self.no_mixed(value, node)
        if inner_type(value.ty).kind == "str":
            return BodyExpr("Call", DeclType("str"), node, "rt.str_repr" if value.ty.kind == "str" else "rt.opt_str_repr").add(value)
        if value.ty.kind == "class" and self.subclass(value.ty.name, "BaseException"):
            return BodyExpr("Call", DeclType("str"), node, "rt.exc_repr").add(value)
        if not self.has_repr(value.ty):
            declaration_error("repr is not supported for " + value.ty.display(), node)
        return self.as_string(value, node)

    def fstring(self, node: Node) -> BodyExpr:
        parts: list[BodyExpr] = []
        for item in node.get("values").items:
            if item.kind == "Constant":
                parts.append(BodyExpr("StrLit", DeclType("str"), item, item.get("value").text))
            else:
                value = self.expr(item.get("value"), DeclType("unknown"))
                conversion = item.get("conversion").text
                if conversion == "114":
                    value = self.repr_value(value, item)
                elif conversion == "115":
                    value = self.as_string(value, item)
                elif conversion != "-1":
                    declaration_error("the f-string !a (ascii) conversion is not supported", item)
                spec = item.get("format_spec")
                text = ""
                if spec.kind != "none":
                    values = [spec] if spec.kind == "Constant" else spec.get("values").items
                    for part in values:
                        if part.kind != "Constant" or part.get("value").kind != "str":
                            declaration_error("f-string format specs must be literal; dynamic width/precision is not supported yet", item)
                        text += part.get("value").text
                parts.append(self.format_value(value, text, item))
        if len(parts) == 0:
            return BodyExpr("StrLit", DeclType("str"), node, "")
        result = parts[0]
        for pos in range(1, len(parts)):
            result = BodyExpr("StrConcat", DeclType("str"), node).add(result).add(parts[pos])
        return result

    def format_value(self, value: BodyExpr, text: str, node: Node) -> BodyExpr:
        if text == "":
            return self.as_string(value, node)
        self.no_mixed(value, node)
        if not numeric_type(value.ty) and value.ty.kind not in ("str", "bool"):
            declaration_error("non-empty format specs on " + value.ty.display() + " need !s or !r", node)
        spec = body_format(text, value.ty.kind, node)
        tail = [self.integer(spec.width, node), self.integer(ord(spec.align), node), BodyExpr("StrLit", DeclType("str"), node, spec.fill)]
        args: list[BodyExpr] = []
        name = ""
        if value.ty.kind == "str":
            name = "__fmt_text"
            args = [value, self.integer(spec.precision, node)]
            args.extend(tail)
        elif spec.kind == "c":
            name = "__fmt_char"
            if value.ty.kind != "i64":
                value = BodyExpr("Convert", DeclType("i64"), node).add(value)
            args = [value]
            args.extend(tail)
        else:
            floating = float_type(value.ty) or spec.kind in ("e", "E", "f", "F", "g", "G", "%")
            target = DeclType("f64" if floating else "i64")
            if not value.ty.same(target):
                value = BodyExpr("Convert", target, node).add(value)
            args = [value, self.integer(ord(spec.kind) if spec.kind != "" else 0, node)]
            name = "__fmt_float" if floating else "__fmt_int"
            if floating:
                args.append(self.integer(spec.precision, node))
            args.append(BodyExpr("Const", DeclType("bool"), node, "true" if spec.alternate else "false"))
            if floating:
                args.append(BodyExpr("Const", DeclType("bool"), node, "true" if spec.normalize_zero else "false"))
            args.append(self.integer(ord(spec.sign), node))
            args.extend(tail)
            args.append(BodyExpr("StrLit", DeclType("str"), node, spec.group))
        self.helper_signature(name)
        result = BodyExpr("Call", DeclType("str"), node, name)
        result.args = args
        return result

    def call(self, node: Node, expected: DeclType) -> BodyExpr:
        func = node.get("func")
        path = self.library_path(func)
        if len(path) > 0 and path[0] != "sys":
            return self.library_call(path, node)
        if func.kind == "Attribute":
            return self.method_call(node)
        if func.kind != "Name":
            return self.call_value(node, self.expr(func, DeclType("unknown")))
        name = func.get("id").text
        if name in self.ctx.bound or name in self.ctx.types:
            return self.call_value(node, self.ctx.get(name, func))
        if self.builtin_mode and name in self.intrinsics:
            return self.intrinsic(name, node)
        if self.builtin_mode and name in self.helpers:
            sig = self.helper_signature(name)
            return self.bind_call(name, sig, node, self.helper_posonly[name])
        if name in ("read_bytes", "write_bytes") and name in self.decl.imported:
            return self.byte_io_call(name, node)
        if name in self.decl.functions:
            return self.bind_call(name, self.decl.functions[name], node)
        if name in self.decl.classes:
            result = self.bind_call(name, self.decl.classes[name].constructor, node)
            call = result.args[0] if result.kind == "Seq" else result
            call.kind = "New"
            call.ty = DeclType("class", name)
            result.ty = call.ty
            return result
        return self.builtin_call(name, node, expected)

    def byte_io_call(self, name: str, node: Node) -> BodyExpr:
        if len(node.get("keywords").items) != 0:
            declaration_error(name + "() takes positional arguments only", node)
        self.arity(node, 0 if name == "read_bytes" else 1, 0 if name == "read_bytes" else 1)
        byte_list = DeclType("list").add(DeclType("i32"))
        result = BodyExpr("Call", byte_list if name == "read_bytes" else DeclType("None"), node, "snek." + name)
        if name == "write_bytes":
            argument = node.get("args").items[0]
            result.add(self.coerce(self.expr(argument, byte_list), byte_list, argument))
        return result

    def builtin_call(self, name: str, node: Node, expected: DeclType) -> BodyExpr:
        args = node.get("args").items
        if name == "wait_all" and name in self.decl.imported:
            if len(node.get("keywords").items) != 0 or len(args) == 0:
                declaration_error("wait_all() requires one or more positional coroutines", node)
            result = BodyExpr("Call", DeclType("Coroutine").add(DeclType("None")), node, "snek.wait_all")
            for arg in args:
                value = self.expr(arg, DeclType("unknown"))
                if value.ty.kind != "Coroutine": declaration_error("wait_all() arguments must be coroutines", arg)
                result.add(value)
            return result
        if name in ("run", "checkpoint") and name in self.decl.imported:
            if len(node.get("keywords").items) != 0:
                declaration_error(name + "() takes positional arguments only", node)
            self.arity(node, 1 if name == "run" else 0, 1 if name == "run" else 0)
            if name == "checkpoint":
                return BodyExpr("Call", DeclType("Coroutine").add(DeclType("None")), node, "snek.checkpoint")
            if self.ctx.function.name != "snek.main":
                declaration_error("run() is only supported at module scope; use await inside async def", node)
            value = self.expr(args[0], DeclType("unknown"))
            if value.ty.kind != "Coroutine":
                declaration_error("run() needs a coroutine", node)
            return BodyExpr("Await", value.ty.args[0], node).add(value)
        if name in ("iter", "next"):
            self.arity(node, 1, 1 if name == "iter" else 2)
            if len(node.get("keywords").items) != 0:
                declaration_error(name + "() takes positional arguments only", node)
            value = self.expr(args[0], DeclType("unknown"))
            if value.ty.kind != "Iterator":
                declaration_error(name + "() currently needs a typed generator", node)
            if name == "iter":
                return value
            result = BodyExpr("MachineOp", value.ty.args[0], node, "next" if len(args) == 1 else "next_default").add(value)
            if len(args) == 2:
                result.add(self.coerce(self.expr(args[1], result.ty), result.ty, args[1]))
            return result
        if name == "sum":
            return self.sum_builtin(node, expected)
        ordered = name == "sorted" or (name in ("min", "max") and len(args) == 1)
        if name in ("min", "max"):
            for kw in node.get("keywords").items:
                if kw.get("arg").text == "key":
                    ordered = True
        if ordered:
            return self.ordered_builtin(name, node, expected)
        if len(node.get("keywords").items) != 0:
            declaration_error(name + "() takes positional arguments here", node)
        if name in ("list", "dict", "tuple"):
            return self.collection_call(name, node, inner_type(expected))
        if name == "type":
            self.arity(node, 1, 1)
            value = self.expr(args[0], DeclType("unknown"))
            if value.ty.kind != "class":
                declaration_error("type() needs a non-optional class instance", node)
            return BodyExpr("TypeOf", DeclType("type").add(value.ty), node).add(value)
        if name == "isinstance":
            if len(args) != 2 or args[1].kind != "Name" or args[1].get("id").text not in self.decl.classes:
                declaration_error("isinstance needs an object and a class name", node)
            cls = args[1].get("id").text
            if cls in self.ctx.bound:
                body_unsupported("isinstance with local class values", node)
            value = self.expr(args[0], DeclType("unknown"))
            if inner_type(value.ty).kind != "class":
                declaration_error("isinstance needs a class instance", node)
            declared = inner_type(value.declared if value.kind == "LocalGet" else value.ty)
            if not self.subclass(cls, declared.name) and not self.subclass(declared.name, cls):
                declaration_error("isinstance is always False for unrelated classes", node)
            return BodyExpr("IsInstance", DeclType("bool"), node, cls).add(value)
        return self.scalar_builtin(name, node, expected)

    def scalar_builtin(self, name: str, node: Node, expected: DeclType) -> BodyExpr:
        args = node.get("args").items
        func = node.get("func")
        if name in ("min", "max"):
            if len(args) != 2:
                body_unsupported("min/max except two scalar arguments", node)
            pair = self.operands(args[0], args[1], expected)
            ty = numeric_join(pair[0].ty, pair[1].ty)
            if ty.kind == "unknown":
                declaration_error(name + " needs compatible numeric arguments", node)
            return BodyExpr("Binary", ty, node, name).add(self.coerce(pair[0], ty, args[0])).add(self.coerce(pair[1], ty, args[1]))
        if name not in ("i32", "i64", "f32", "f64", "bool", "str", "repr", "len", "abs", "ord", "chr"):
            if (name in self.decl.library_imports or name in self.decl.imported or name in self.decl.classes
                    or name in ("list", "dict", "tuple", "isinstance", "sorted", "sum", "iter", "next", "type", "super")):
                body_unsupported("call to " + name, node)
            declaration_error("undefined or unsupported function '" + name + "'", func)
        if len(args) != 1:
            declaration_error(name + "() takes one argument", node)
        if name in ("i32", "i64", "f32", "f64"):
            return self.numeric_conversion(name, node)
        value = self.expr(args[0], expected if name == "abs" and not integer_node(args[0]) else DeclType("unknown"))
        if name == "str" or name == "repr":
            if name == "repr":
                return self.repr_value(value, node)
            return self.as_string(value, node)
        if name == "len":
            return self.length(value, node)
        if name == "ord":
            if value.ty.kind != "str":
                declaration_error(name + " needs a non-optional str in the scalar checker", node)
            return BodyExpr("Call", DeclType("i32"), node, "rt.ord").add(value)
        if name == "chr":
            value = self.coerce(value, DeclType("i32"), args[0])
            if value.kind == "Const":
                point = i32(value.text)
                if point >= 0 and point < 1114112:
                    self.unicode_extra.append(point)
            else:
                self.unicode_dynamic = True
            return BodyExpr("Call", DeclType("str"), node, "rt.chr").add(value)
        if name == "abs":
            if not numeric_type(value.ty):
                declaration_error("abs needs a number", node)
            result = BodyExpr("Unary", value.ty, node, "abs").add(value)
            result.mixed = value.mixed
            return result
        if name == "bool":
            if value.ty.kind == "bool":
                return value
            if value.ty.kind == "class" and "__len__" not in self.decl.classes[value.ty.name].methods:
                for cls in self.decl.class_order:
                    if self.subclass(cls, value.ty.name) and "__len__" in self.decl.classes[cls].methods:
                        declaration_error("bool needs __len__ declared on the static base type", node)
                return self.sequence_expr([BodyStmt("ExprStmt", node).add(value)], BodyExpr("Const", DeclType("bool"), node, "true"))
            if value.ty.kind in ("str", "list", "tuple", "dict", "view", "class"):
                value = self.length(value, node)
            if not numeric_type(value.ty):
                body_unsupported("bool conversion from " + value.ty.display(), node)
            zero = BodyExpr("Const", value.ty, node, "f64:0,0,0,0,0,0,0,0" if float_type(value.ty) else "0")
            return BodyExpr("Compare", DeclType("bool"), node, "ne").add(value).add(zero)
        body_unsupported("call " + name, node)
        return value

    def numeric_conversion(self, name: str, node: Node) -> BodyExpr:
        if name not in self.decl.imported and not self.builtin_mode:
            declaration_error("'" + name + "' must be imported", node.get("func"))
        target = DeclType(name)
        value = self.expr(node.get("args").items[0], target)
        if value.ty.kind == "str" and int_type(target):
            self.helper_signature("__parse_int")
            parsed = BodyExpr("Call", DeclType("i64"), node, "__parse_int").add(value).add(self.integer(32 if name == "i32" else 64, node))
            return parsed if name == "i64" else BodyExpr("Convert", target, node).add(parsed)
        if not numeric_type(value.ty) and value.ty.kind != "bool":
            body_unsupported("numeric conversion from " + value.ty.display(), node)
        if value.ty.same(target):
            value.mixed = False
            return value
        return BodyExpr("Convert", target, node).add(value)

    def list_element(self, value: BodyExpr, node: Node) -> DeclType:
        if value.ty.kind != "list":
            declaration_error("builtin needs a non-optional list, got " + value.ty.display(), node)
        return value.ty.args[0]

    def ordered_builtin(self, name: str, node: Node, expected: DeclType) -> BodyExpr:
        keywords: dict[str, Node] = {}
        for kw in node.get("keywords").items:
            key = kw.get("arg")
            allowed = key.text in ("key", "reverse") if name == "sorted" else key.text in ("key", "default")
            if key.kind == "none" or not allowed:
                declaration_error(name + "() got an unexpected keyword argument", kw)
            if key.text in keywords:
                declaration_error(name + "() got multiple values for '" + key.text + "'", kw)
            keywords[key.text] = kw.get("value")
        args = node.get("args").items
        self.arity(node, 1, 1 if name == "sorted" else 2)
        if len(args) == 2 and "default" in keywords:
            declaration_error(name + "() cannot use default with multiple positional arguments", node)
        has_key = "key" in keywords and not none_node(keywords["key"])
        key_value = BodyExpr("Missing", DeclType("unknown"), node)
        if has_key and keywords["key"].kind != "Lambda":
            key_value = self.expr(keywords["key"], DeclType("unknown"))
            if key_value.ty.kind != "Callable":
                declaration_error(name + "() key must be a non-optional Callable", keywords["key"])
            if len(key_value.ty.args) != 2:
                declaration_error(name + "() key must take exactly one argument", keywords["key"])
        source = args[0]
        if len(args) == 2:
            source = Node("List", "", node.line, node.column).put("elts", sequence(args))
        hint = expected if name == "sorted" and expected.kind == "list" else DeclType("unknown")
        fallback = BodyExpr("Missing", DeclType("unknown"), node)
        if source.kind == "List" and len(source.get("elts").items) == 0 and hint.kind == "unknown":
            if "default" in keywords and key_value.kind == "Missing":
                fallback = self.expr(keywords["default"], expected)
            elem = key_value.ty.args[0] if key_value.kind != "Missing" else fallback.ty if fallback.kind != "Missing" else expected if name != "sorted" else DeclType("unknown")
            if elem.kind == "None":
                declaration_error("an empty list with a None default needs a list element type", source)
            if elem.kind == "unknown" and name != "sorted":
                elem = DeclType("i32")
            if elem.kind != "unknown":
                hint = DeclType("list").add(elem)
        xs = self.expr(source, hint)
        elem = self.list_element(xs, source)
        if "default" in keywords and fallback.kind == "Missing":
            fallback = self.expr(keywords["default"], elem)
        comparable = elem
        if has_key:
            if key_value.kind == "Missing":
                key_value = self.key_lambda(keywords["key"], elem)
            if not self.assignable(elem, key_value.ty.args[0]):
                declaration_error(name + "() key must accept " + elem.display(), keywords["key"])
            comparable = key_value.ty.args[-1]
        if not numeric_type(comparable) and comparable.kind not in ("bool", "str"):
            declaration_error(name + "() " + ("key must return a number, bool, or str" if has_key else "supports lists of numbers, bools or strings"), source)
        ret = xs.ty if name == "sorted" else elem
        if fallback.kind != "Missing":
            if elem.reference() and (fallback.ty.kind == "None" or fallback.ty.same(optional_type(elem))):
                ret = optional_type(elem)
            fallback = self.coerce(fallback, ret, keywords["default"])
        # Type inference above may inspect the default first, but runtime
        # evaluation always captures the source before its keyword arguments.
        pre: list[BodyStmt] = []
        xs = self.capture(xs, pre)
        reverse = BodyExpr("Const", DeclType("bool"), node, "false")
        for kw in node.get("keywords").items:
            keyword_name = kw.get("arg").text
            if keyword_name == "reverse":
                reverse = self.capture(self.condition(kw.get("value")), pre)
            elif keyword_name == "key" and has_key:
                key_value = self.capture(key_value, pre)
            elif keyword_name == "default":
                fallback = self.capture(fallback, pre)
        op = name
        if fallback.kind != "Missing":
            op += "_default" if ret.same(elem) else "_optional"
        result = BodyExpr("KeyedListOp" if has_key else "ListOp", ret, node, op).add(xs)
        if has_key:
            result.add(key_value)
        if name == "sorted":
            result.add(reverse)
        elif fallback.kind != "Missing":
            result.add(fallback)
        return self.sequence_expr(pre, result)

    def sum_builtin(self, node: Node, expected: DeclType) -> BodyExpr:
        keywords: dict[str, Node] = {}
        for kw in node.get("keywords").items:
            key = kw.get("arg")
            if key.kind == "none" or key.text != "start":
                declaration_error("sum() got an unexpected keyword argument", kw)
            if key.text in keywords:
                declaration_error("sum() got multiple values for 'start'", kw)
            keywords[key.text] = kw.get("value")
        self.arity(node, 1, 2)
        args = node.get("args").items
        if len(args) == 2 and "start" in keywords:
            declaration_error("sum() got multiple values for 'start'", node)
        start_node = args[1] if len(args) == 2 else keywords.get("start", missing())
        start = BodyExpr("Missing", DeclType("unknown"), node)
        if start_node.kind != "none":
            start = self.expr(start_node, DeclType("unknown"))
        hint = DeclType("unknown")
        if args[0].kind == "List" and len(args[0].get("elts").items) == 0:
            elem = start.ty if start.kind != "Missing" else expected if expected.kind != "unknown" else DeclType("i32")
            hint = DeclType("list").add(elem)
        xs = self.expr(args[0], hint)
        elem = self.list_element(xs, args[0])
        if not numeric_type(elem) and elem.kind != "bool":
            declaration_error("sum() needs a list of numbers or bools", args[0])
        nonempty = xs.kind == "ListLit" and len(xs.args) > 0
        if start.kind != "Missing" and not numeric_type(start.ty):
            declaration_error("sum() start must be numeric; convert bool explicitly", start_node)
        if start.kind != "Missing" and not (float_type(elem) and nonempty):
            self.no_mixed(start, start_node)
        if float_type(elem):
            if (start.kind == "Missing" or not float_type(start.ty)) and not nonempty:
                declaration_error("sum() of a possibly empty float list needs a float start", node)
            ret = DeclType("f64")
            if start.kind == "Missing":
                start = BodyExpr("Const", ret, node, "f64:0,0,0,0,0,0,0,0")
        else:
            if start.kind == "Missing":
                start = self.integer(0, node)
            ret = numeric_join(DeclType("i32") if elem.kind == "bool" else elem, start.ty)
            if ret.kind == "unknown":
                declaration_error("sum() cannot mix " + elem.display() + " elements and " + start.ty.display() + " start", node)
        start = self.coerce(start, ret, node if start_node.kind == "none" else start_node, True)
        # The emitter evaluates the list operand before start, including
        # mutations performed by start before the reduction begins.
        return BodyExpr("ListOp", ret, node, "sum_" + ret.kind).add(xs).add(start)

    def sequence_expr(self, pre: list[BodyStmt], value: BodyExpr) -> BodyExpr:
        if len(pre) == 0:
            return value
        result = BodyExpr("Seq", value.ty, value.node).add(value)
        result.pre = pre
        return result

    def capture(self, value: BodyExpr, pre: list[BodyStmt]) -> BodyExpr:
        temp = self.ctx.temp(value.ty, value.node)
        pre.append(BodyStmt("SetLocal", value.node, temp).add(value))
        return self.ctx.get(temp, value.node)

    def index(self, node: Node) -> BodyExpr:
        return self.coerce(self.expr(node, DeclType("i32")), DeclType("i32"), node)

    def integer(self, value: i32, node: Node) -> BodyExpr:
        return BodyExpr("Const", DeclType("i32"), node, str(value))

    def has_repr(self, ty: DeclType) -> bool:
        if ty.kind in ("class", "TracebackType", "type", "Iterator", "Coroutine", "Callable", "Stream", "HostObject"):
            return False
        if ty.kind == "view":
            return self.has_repr(self.view_element(ty))
        for arg in ty.args:
            if not self.has_repr(arg):
                return False
        return True

    def equality(self, left: DeclType, right: DeclType, node: Node) -> None:
        a = inner_type(left)
        b = inner_type(right)
        if a.kind == "TracebackType" or b.kind == "TracebackType":
            declaration_error("tracebacks are opaque; only None checks are supported", node)
        if a.kind == "class" and b.kind == "class":
            for name in self.decl.class_order:
                cls = self.decl.classes[name]
                if "__eq__" in cls.methods:
                    param = cls.methods["__eq__"].sig.params[0].ty
                    if ((self.subclass(name, a.name) and not self.assignable(right, param))
                            or (self.subclass(name, b.name) and not self.assignable(left, param))):
                        declaration_error(name + ".__eq__ cannot accept the compared operand", node)
        elif a.kind in ("list", "tuple", "dict"):
            for arg in a.args:
                self.equality(arg, arg, node)

    def elements(self, nodes: list[Node], hint: DeclType, node: Node) -> BodyExpr:
        if hint.kind == "unknown":
            # Empty nested lists get their context from a nonempty sibling.
            for child in nodes:
                if child.kind != "List" or len(child.get("elts").items) != 0:
                    value = self.expr(child, DeclType("unknown"))
                    hint = value.ty if hint.kind == "unknown" else self.join(hint, value.ty)
                    if hint.kind == "unknown":
                        declaration_error("collection elements have incompatible types", child)
            if hint.kind in ("unknown", "None"):
                declaration_error("cannot infer collection element type; add an annotation", node)
        result = BodyExpr("ListLit", DeclType("list").add(hint), node)
        # Rechecking does not run the expressions. Only this final typed tree
        # is emitted, in source order (including dict key/value interleaving).
        for child in nodes:
            result.add(self.coerce(self.expr(child, hint), hint, child))
        return result

    def collection(self, node: Node, expected: DeclType) -> BodyExpr:
        if node.kind == "List":
            hint = expected.args[0] if expected.kind == "list" else DeclType("unknown")
            return self.elements(node.get("elts").items, hint, node)
        if node.kind == "Tuple":
            items = node.get("elts").items
            if expected.kind == "tuple" and len(items) != len(expected.args):
                declaration_error("tuple arity does not match annotation", node)
            result = BodyExpr("TupleLit", DeclType("tuple"), node)
            for index in range(len(items)):
                hint = expected.args[index] if expected.kind == "tuple" else DeclType("unknown")
                value = self.expr(items[index], hint)
                if hint.kind != "unknown":
                    value = self.coerce(value, hint, items[index])
                else:
                    self.no_mixed(value, items[index])
                if value.ty.kind == "None":
                    declaration_error("cannot infer tuple element from None", items[index])
                result.ty.add(hint if hint.kind != "unknown" else value.ty)
                result.add(value)
            return result
        keys = node.get("keys").items
        for key in keys:
            if key.kind == "none":
                declaration_error("dictionary unpacking is not supported", node)
        key_hint = expected.args[0] if expected.kind == "dict" else DeclType("unknown")
        value_hint = expected.args[1] if expected.kind == "dict" else DeclType("unknown")
        checked_keys = self.elements(keys, key_hint, node)
        checked_values = self.elements(node.get("values").items, value_hint, node)
        key_type = checked_keys.ty.args[0]
        if not key_type.hashable():
            declaration_error("dictionary key type is not hashable: " + key_type.display(), node)
        result = BodyExpr("DictLit", DeclType("dict").add(key_type).add(checked_values.ty.args[0]), node)
        for index in range(len(keys)):
            result.add(checked_keys.args[index]).add(checked_values.args[index])
        return result

    def homogeneous(self, ty: DeclType, node: Node) -> DeclType:
        if len(ty.args) == 0:
            declaration_error("operation needs a non-empty homogeneous tuple", node)
        for arg in ty.args:
            if not arg.same(ty.args[0]):
                declaration_error("operation needs a homogeneous tuple; use a literal index or unpack", node)
        return ty.args[0]

    def slice_args(self, node: Node) -> list[BodyExpr]:
        result: list[BodyExpr] = []
        flags = 0
        for name in ("lower", "upper"):
            part = node.get(name)
            present = part.kind != "none" and not none_node(part)
            result.append(self.index(part) if present else self.integer(0, node))
            if present:
                flags |= 1 if name == "lower" else 2
        part = node.get("step")
        result.append(self.index(part) if part.kind != "none" and not none_node(part) else self.integer(1, node))
        result.append(self.integer(flags, node))
        return result

    def subscript(self, obj: BodyExpr, index: Node, node: Node) -> BodyExpr:
        ty = obj.ty
        if index.kind == "Slice":
            if ty.kind == "tuple":
                return self.tuple_slice(obj, index, node)
            if ty.kind not in ("str", "list"):
                body_unsupported("slicing " + ty.display(), node)
            value = BodyExpr("Call" if ty.kind == "str" else "ListOp", ty, node,
                             "rt.str_slice" if ty.kind == "str" else "slice").add(obj)
            value.args.extend(self.slice_args(index))
            return value
        if ty.kind == "dict":
            key = self.coerce(self.expr(index, ty.args[0]), ty.args[0], index)
            return BodyExpr("DictOp", ty.args[1], node, "get").add(obj).add(key)
        if ty.kind == "list":
            return BodyExpr("ListGet", ty.args[0], node).add(obj).add(self.index(index))
        if ty.kind == "tuple":
            idx = self.index(index)
            if idx.kind == "Const":
                position = i32(idx.text)
                if position < 0:
                    position += len(ty.args)
                if position < 0 or position >= len(ty.args):
                    declaration_error("tuple index out of range", node)
                return BodyExpr("TupleGet", ty.args[position], node, str(position)).add(obj)
            return BodyExpr("TupleOp", self.homogeneous(ty, node), node, "get").add(obj).add(idx)
        if ty.kind == "str":
            return BodyExpr("Call", ty, node, "rt.str_get").add(obj).add(self.index(index))
        declaration_error("cannot index " + ty.display() + "; narrow optional values first", node)
        return obj

    def tuple_slice(self, obj: BodyExpr, index: Node, node: Node) -> BodyExpr:
        size = i64(len(obj.ty.args))
        bounds: list[i64] = []
        present: list[bool] = []
        for name in ("lower", "upper", "step"):
            part = index.get(name)
            supplied = part.kind != "none" and not none_node(part)
            present.append(supplied)
            if supplied and not integer_node(part):
                declaration_error("tuple slice bounds must be integer literals (the result has fixed length)", part)
            bounds.append(self.tuple_bound(part, size + 1) if supplied else i64(0))
        step = bounds[2] if present[2] else i64(1)
        if step == 0:
            declaration_error("tuple slice step cannot be zero", index)
        # Clamp before advancing, avoiding overflow even at the i64 limits.
        step = max(-size - 1, min(size + 1, step))
        for pos in range(2):
            if present[pos]:
                value = bounds[pos]
                if value < 0:
                    value += size
                bounds[pos] = max(i64(0), min(size, value)) if step > 0 else max(i64(-1), min(size - 1, value))
            else:
                bounds[pos] = (i64(0) if pos == 0 else size) if step > 0 else (size - 1 if pos == 0 else i64(-1))
        pre: list[BodyStmt] = []
        obj = self.capture(obj, pre)
        result = BodyExpr("TupleLit", DeclType("tuple"), node)
        at = bounds[0]
        while (at < bounds[1] if step > 0 else at > bounds[1]):
            field = obj.ty.args[i32(at)]
            result.ty.add(field)
            result.add(BodyExpr("TupleGet", field, node, str(at)).add(obj))
            at += step
        return self.sequence_expr(pre, result)

    def tuple_bound(self, node: Node, limit: i64) -> i64:
        negative = False
        while node.kind == "UnaryOp":
            if node.get("op").kind == "USub":
                negative = not negative
            node = node.get("operand")
        text = node.get("value").get("text").text.replace("_", "").lower()
        base = 10
        if text.startswith("0x"):
            base = 16
        elif text.startswith("0o"):
            base = 8
        elif text.startswith("0b"):
            base = 2
        if base != 10:
            text = text[2:]
        value: i64 = 0
        for char in text:
            value = min(limit, value * base + "0123456789abcdef".find(char))
        return -value if negative else value

    def length(self, value: BodyExpr, node: Node) -> BodyExpr:
        ty = value.ty
        if ty.kind in ("list", "str"):
            return BodyExpr("ListLen" if ty.kind == "list" else "StrLen", DeclType("i32"), node).add(value)
        if ty.kind in ("dict", "view"):
            return BodyExpr("DictOp", DeclType("i32"), node, "len").add(value)
        if ty.kind == "tuple":
            return self.sequence_expr([BodyStmt("ExprStmt", node).add(value)], self.integer(len(ty.args), node))
        if ty.kind == "class" and "__len__" in self.decl.classes[ty.name].methods:
            ret = self.decl.classes[ty.name].methods["__len__"].sig.ret
            pre: list[BodyStmt] = []
            length = self.capture(BodyExpr("CallMethod", ret, node, "__len__").add(value), pre)
            invalid = BodyExpr("Compare", DeclType("bool"), node, "lt").add(length).add(BodyExpr("Const", ret, node, "0"))
            stmt = BodyStmt("If", node).add(invalid)
            exc = BodyExpr("New", DeclType("class", "ValueError"), node, "ValueError")
            exc.add(BodyExpr("StrLit", DeclType("str"), node, "__len__() should return >= 0"))
            stmt.body.append(BodyStmt("Raise", node).add(exc))
            pre.append(stmt)
            return self.sequence_expr(pre, length)
        declaration_error("len needs a non-optional collection, string or __len__ method", node)
        return value

    def view_element(self, ty: DeclType) -> DeclType:
        mapping = ty.args[0]
        if ty.name == "items":
            return DeclType("tuple").add(mapping.args[0]).add(mapping.args[1])
        return mapping.args[1] if ty.name == "values" else mapping.args[0]

    def contains(self, left: Node, right: Node, node: Node) -> BodyExpr:
        obj = self.expr(right, DeclType("unknown"))
        ty = obj.ty
        elem = DeclType("unknown")
        kind = ""
        if ty.kind == "str":
            elem = ty
            kind = "Call"
        elif ty.kind in ("list", "dict"):
            elem = ty.args[0]
            kind = "ListOp" if ty.kind == "list" else "DictOp"
        elif ty.kind == "view":
            elem = self.view_element(ty)
            kind = "DictOp"
        elif ty.kind == "tuple":
            elem = self.homogeneous(ty, node)
            kind = "TupleOp"
        else:
            declaration_error("membership needs a non-optional container", node)
        self.equality(elem, elem, node)
        value = self.coerce(self.expr(left, elem), elem, left, ty.kind == "list")
        pre: list[BodyStmt] = []
        value = self.capture(value, pre)
        result = BodyExpr(kind, DeclType("bool"), node, "rt.str_contains" if ty.kind == "str" else "contains").add(obj).add(value)
        return self.sequence_expr(pre, result)

    def collection_call(self, name: str, node: Node, expected: DeclType) -> BodyExpr:
        args = node.get("args").items
        if len(args) == 0:
            literal = Node("List" if name == "list" else "Dict" if name == "dict" else "Tuple", "", node.line, node.column)
            literal.put("elts", sequence([])).put("keys", sequence([])).put("values", sequence([]))
            return self.collection(literal, DeclType("tuple") if name == "tuple" else expected)
        if len(args) != 1:
            declaration_error(name + "() takes zero or one argument", node)
        obj = self.expr(args[0], expected)
        if name == "list" and obj.ty.kind == "Iterator":
            pre: list[BodyStmt] = []
            obj = self.capture(obj, pre)
            result = self.capture(BodyExpr("ListLit", DeclType("list").add(obj.ty.args[0]), node), pre)
            loop = BodyStmt("Loop", node).add(BodyExpr("MachineOp", DeclType("bool"), node, "advance").add(obj))
            append = BodyExpr("ListAppend", DeclType("None"), node).add(result).add(BodyExpr("MachineOp", obj.ty.args[0], node, "value").add(obj))
            loop.body.append(BodyStmt("ExprStmt", node).add(append))
            pre.append(loop)
            return self.sequence_expr(pre, result)
        if obj.ty.kind == name:
            if name == "tuple":
                return obj
            return BodyExpr("ListOp" if name == "list" else "DictOp", obj.ty, node, "copy").add(obj)
        if name == "list" and obj.ty.kind in ("dict", "view"):
            elem = obj.ty.args[0] if obj.ty.kind == "dict" else self.view_element(obj.ty)
            return BodyExpr("DictOp", DeclType("list").add(elem), node, "list").add(obj)
        if name == "list" and obj.ty.kind == "tuple":
            elem = expected.args[0] if expected.kind == "list" else self.homogeneous(obj.ty, node)
            pre: list[BodyStmt] = []
            obj = self.capture(obj, pre)
            result = BodyExpr("ListLit", DeclType("list").add(elem), node)
            for index in range(len(obj.ty.args)):
                result.add(self.coerce(BodyExpr("TupleGet", obj.ty.args[index], node, str(index)).add(obj), elem, node))
            return self.sequence_expr(pre, result)
        body_unsupported(name + " conversion from " + obj.ty.display(), node)
        return obj

    def library_path(self, node: Node) -> list[str]:
        parts: list[str] = []
        while node.kind == "Attribute":
            parts.insert(0, node.get("attr").text)
            node = node.get("value")
        if node.kind != "Name":
            return []
        name = node.get("id").text
        if name not in self.decl.library_imports or name in self.ctx.bound or name in self.ctx.types:
            return []
        return self.decl.library_imports[name] + parts

    def library_call(self, path: list[str], node: Node) -> BodyExpr:
        name = ".".join(path)
        args = node.get("args").items
        if len(node.get("keywords").items) != 0:
            declaration_error(name + "() takes positional arguments only", node)
        if name == "os.getenv":
            self.arity(node, 1, 2)
            key = self.coerce(self.expr(args[0], DeclType("str")), DeclType("str"), args[0])
            optional = optional_type(DeclType("str"))
            default = self.expr(args[1], DeclType("unknown")) if len(args) == 2 else BodyExpr("NoneLit", optional, node)
            if default.ty.kind not in ("str", "None") and not default.ty.same(optional):
                declaration_error("os.getenv() default must be str or None", node)
            ret = DeclType("str") if default.ty.kind == "str" else optional
            pre: list[BodyStmt] = []
            key = self.capture(key, pre)
            result = BodyExpr("Call", ret, node, "snek.lib.getenv-required" if ret.kind == "str" else "snek.lib.getenv")
            return self.sequence_expr(pre, result.add(key).add(self.coerce(default, optional, node)))
        if name in ("time.time", "time.monotonic"):
            self.arity(node, 0, 0)
            return BodyExpr("Call", DeclType("f64"), node, "snek.lib." + path[-1])
        if name == "time.sleep":
            self.arity(node, 1, 1)
            value = self.coerce(self.expr(args[0], DeclType("f64")), DeclType("f64"), args[0], True)
            return BodyExpr("Call", DeclType("Coroutine").add(DeclType("None")), node, "snek.lib.sleep").add(value)
        if name in ("secrets.token_bytes", "secrets.token_hex", "secrets.randbelow"):
            self.arity(node, 1, 1)
            value = self.coerce(self.expr(args[0], DeclType("i32")), DeclType("i32"), args[0])
            ret = DeclType("list").add(DeclType("i32")) if path[1] == "token_bytes" else DeclType("str") if path[1] == "token_hex" else DeclType("i32")
            return BodyExpr("Call", ret, node, "snek.lib." + path[1]).add(value)
        declaration_error("unsupported standard-library operation '" + name + "'", node)
        return BodyExpr("NoneLit", DeclType("None"), node)

    def stream_call(self, recv: BodyExpr, name: str, node: Node) -> BodyExpr:
        if len(node.get("keywords").items) != 0:
            declaration_error("stream methods take positional arguments only", node)
        binary = recv.ty.name.startswith("Binary")
        output = recv.ty.name.endswith("Output")
        if name in ("close", "flush", "drain") and (output or name == "close"):
            self.arity(node, 0, 0)
            ret = DeclType("Coroutine").add(DeclType("None")) if name == "drain" else DeclType("None")
            return BodyExpr("Call", ret, node, "snek.sys.stream_" + name).add(recv)
        if output and name == "write":
            self.arity(node, 1, 1)
            arg = node.get("args").items[0]
            ty = DeclType("list").add(DeclType("i32")) if binary else DeclType("str")
            value = self.coerce(self.expr(arg, ty), ty, arg)
            pre: list[BodyStmt] = []
            recv = self.capture(recv, pre)
            call = BodyExpr("Call", DeclType("i32"), node, "snek.sys.stream_write_bytes" if binary else "snek.sys.stream_write")
            return self.sequence_expr(pre, call.add(recv).add(value))
        if binary and not output and name in ("read", "readline"):
            self.arity(node, 0, 1)
            args = node.get("args").items
            size = self.index(args[0]) if len(args) > 0 else self.integer(-1, node)
            pre = []
            recv = self.capture(recv, pre)
            ret = DeclType("Coroutine").add(DeclType("list").add(DeclType("i32")))
            call = BodyExpr("Call", ret, node, "snek.sys.stream_read").add(recv).add(size)
            return self.sequence_expr(pre, call.add(BodyExpr("Const", DeclType("bool"), node, "true" if name == "readline" else "false")))
        declaration_error("unsupported stream operation '" + recv.ty.display() + "." + name + "'", node)
        return recv

    def field(self, obj: BodyExpr, name: str, node: Node) -> DeclField:
        if obj.ty.kind != "class":
            declaration_error("field access needs a non-optional class instance", node)
        cls = self.decl.classes[obj.ty.name]
        if name not in cls.fields or name.startswith("__"):
            declaration_error(cls.name + " has no accessible field '" + name + "'", node)
        return cls.fields[name]

    def arity(self, node: Node, low: i32, high: i32) -> None:
        count = len(node.get("args").items)
        if count < low or count > high:
            declaration_error("wrong number of arguments", node)

    def method_call(self, node: Node) -> BodyExpr:
        func = node.get("func")
        recv_node = func.get("value")
        name = func.get("attr").text
        direct = False
        if recv_node.kind == "Call" and named(recv_node.get("func"), "super"):
            if self.ctx.function.owner == "" or len(recv_node.get("args").items) != 0 or len(recv_node.get("keywords").items) != 0:
                declaration_error("super() requires a method and no arguments", node)
            if name == "__init__":
                declaration_error("super().__init__ must be a top-level constructor statement before self is used", node)
            base = self.decl.classes[self.ctx.function.owner].base
            if base == "" or name not in self.decl.classes[base].methods:
                if base != "" and name == "__str__" and self.subclass(base, "BaseException"):
                    self.arity(node, 0, 0)
                    if len(node.get("keywords").items) != 0:
                        declaration_error("__str__() takes no keyword arguments", node)
                    return BodyExpr("Call", DeclType("str"), node, "rt.exc_str_default").add(self.ctx.get("self", node))
                declaration_error("base class has no method '" + name + "'", node)
            recv = self.ctx.get("self", node)
            method = self.decl.classes[base].methods[name]
            direct = True
        else:
            recv = self.expr(recv_node, DeclType("unknown"))
            if recv.ty.kind == "Stream":
                return self.stream_call(recv, name, node)
            if recv.ty.kind == "Iterator":
                if len(node.get("keywords").items) != 0:
                    declaration_error("generator methods take positional arguments only", node)
                self.arity(node, 0 if name == "close" else 1, 0 if name == "close" else 1)
                if name == "close":
                    return BodyExpr("MachineOp", DeclType("None"), node, "close").add(recv)
                arg = node.get("args").items[0]
                if name == "send":
                    if not none_node(arg):
                        declaration_error("only generator.send(None) is supported", node)
                    return BodyExpr("MachineOp", recv.ty.args[0], node, "next").add(recv)
                if name == "throw":
                    value = self.expr(arg, DeclType("unknown"))
                    if value.ty.kind != "class" or not self.subclass(value.ty.name, "BaseException"):
                        declaration_error("generator.throw() needs an exception instance", node)
                    return BodyExpr("MachineOp", recv.ty.args[0], node, "throw").add(recv).add(value)
                declaration_error("unsupported generator method", node)
            if recv.ty.kind == "str":
                helper = "__str_" + name
                if name.startswith("_") or helper not in self.helpers:
                    body_unsupported("str." + name + "()", node)
                sig = self.helper_signature(helper)
                method_sig = DeclSignature()
                method_sig.params = sig.params[1:]
                method_sig.ret = sig.ret
                pre: list[BodyStmt] = []
                recv = self.capture(recv, pre)
                bound = self.bind_call(helper, method_sig, node, max(self.helper_posonly[helper] - 1, 0))
                call = bound
                if bound.kind == "Seq":
                    pre.extend(bound.pre)
                    call = bound.args[0]
                call.args.insert(0, recv)
                return self.sequence_expr(pre, call)
            if recv.ty.kind != "class":
                return self.collection_method(recv, name, node)
            cls = self.decl.classes[recv.ty.name]
            if name not in cls.methods:
                if name in cls.fields:
                    return self.call_value(node, BodyExpr("GetField", cls.fields[name].ty, node, name).add(recv))
                declaration_error(cls.name + " has no method '" + name + "'", node)
            method = cls.methods[name]
        pre: list[BodyStmt] = []
        recv = self.capture(recv, pre)
        bound = self.bind_call(method.owner + "." + name, method.sig, node)
        call = bound
        if bound.kind == "Seq":
            pre.extend(bound.pre)
            call = bound.args[0]
        call.args.insert(0, recv)
        if not direct:
            call.kind = "CallMethod"
            call.text = name
        return self.sequence_expr(pre, call)

    def collection_method(self, recv: BodyExpr, name: str, node: Node) -> BodyExpr:
        args = node.get("args").items
        ty = recv.ty
        if ty.kind not in ("list", "dict", "tuple"):
            body_unsupported("methods of " + ty.display(), node)
        if len(node.get("keywords").items) != 0:
            declaration_error("collection methods take positional arguments", node)
        if ty.kind == "list":
            elem = ty.args[0]
            if name in ("index", "count", "remove"):
                self.equality(elem, elem, node)
            if name in ("append", "count", "remove", "extend", "insert", "pop", "index"):
                self.arity(node, 0 if name == "pop" else 2 if name == "insert" else 1,
                           3 if name == "index" else 2 if name == "insert" else 1)
                if name == "pop":
                    if len(args) == 0:
                        return BodyExpr("ListPop", elem, node).add(recv)
                    return BodyExpr("ListOp", elem, node, "pop_at").add(recv).add(self.index(args[0]))
                if name == "extend":
                    return BodyExpr("ListOp", DeclType("None"), node, name).add(recv).add(self.coerce(self.expr(args[0], ty), ty, args[0]))
                if name == "insert":
                    return (BodyExpr("ListOp", DeclType("None"), node, name).add(recv).add(self.index(args[0]))
                            .add(self.coerce(self.expr(args[1], elem), elem, args[1])))
                value = self.coerce(self.expr(args[0], elem), elem, args[0], name in ("count", "remove"))
                result = BodyExpr("ListAppend" if name == "append" else "ListOp",
                                  DeclType("i32" if name in ("index", "count") else "None"), node, name).add(recv).add(value)
                if name == "index":
                    if not self.has_repr(elem):
                        declaration_error("list.index requires elements with repr", node)
                    result.add(self.index(args[1]) if len(args) > 1 else self.integer(0, node))
                    result.add(self.index(args[2]) if len(args) > 2 else self.integer(2147483647, node))
                return result
            if name in ("copy", "clear", "reverse"):
                self.arity(node, 0, 0)
                return BodyExpr("ListOp", ty if name == "copy" else DeclType("None"), node, name).add(recv)
        if ty.kind == "tuple" and name in ("count", "index"):
            self.arity(node, 1, 3 if name == "index" else 1)
            elem = self.homogeneous(ty, node)
            self.equality(elem, elem, node)
            result = BodyExpr("TupleOp", DeclType("i32"), node, name).add(recv)
            result.add(self.coerce(self.expr(args[0], elem), elem, args[0], True))
            if name == "index":
                result.add(self.index(args[1]) if len(args) > 1 else self.integer(0, node))
                result.add(self.index(args[2]) if len(args) > 2 else self.integer(len(ty.args), node))
            return result
        if ty.kind == "dict":
            if name in ("copy", "clear", "popitem", "keys", "values", "items"):
                self.arity(node, 0, 0)
                ret = DeclType("None")
                if name == "copy":
                    ret = ty
                elif name == "popitem":
                    ret = DeclType("tuple").add(ty.args[0]).add(ty.args[1])
                elif name in ("keys", "values", "items"):
                    ret = DeclType("view", name).add(ty)
                return BodyExpr("DictOp", ret, node, name).add(recv)
            if name == "update":
                self.arity(node, 1, 1)
                return BodyExpr("DictOp", DeclType("None"), node, name).add(recv).add(self.coerce(self.expr(args[0], ty), ty, args[0]))
            if name in ("get", "pop", "setdefault"):
                self.arity(node, 1, 2)
                key = self.coerce(self.expr(args[0], ty.args[0]), ty.args[0], args[0])
                ret = ty.args[1]
                if len(args) == 2:
                    default = self.expr(args[1], ret)
                    if name != "setdefault" and ret.reference() and (default.ty.kind == "None" or default.ty.same(optional_type(ret))):
                        ret = optional_type(ret)
                    default = self.coerce(default, ret, args[1])
                    op = name + ("_default" if ret.same(ty.args[1]) else "_optional")
                    return BodyExpr("DictOp", ret, node, op).add(recv).add(key).add(default)
                if name == "pop":
                    return BodyExpr("DictOp", ret, node, name).add(recv).add(key)
                if (name == "setdefault" and ret.kind != "optional") or not ret.reference():
                    declaration_error("dict." + name + " needs an explicit default for non-optional values", node)
                ret = optional_type(ret)
                op = "get_optional" if name == "get" else "setdefault_default"
                return BodyExpr("DictOp", ret, node, op).add(recv).add(key).add(BodyExpr("NoneLit", ret, node))
        body_unsupported(ty.kind + "." + name + "()", node)
        return recv

    def constructor(self, name: str) -> None:
        cls = self.decl.classes[name]
        if self.subclass(name, "BaseException") and cls.init_node.kind != "none":
            found_super = False
            for node in cls.init_node.get("body").items:
                if super_initializer(node).kind != "none":
                    found_super = True
            if not found_super:
                declaration_error(name + ".__init__ must call super().__init__(message)", cls.init_node)
        sig = DeclSignature()
        sig.params = cls.constructor.params
        sig.ret = DeclType("class", name)
        for param in sig.params:
            body_type(param.ty, cls.node)
        self.ctx = BodyContext(name + ".__new__", sig)
        self.ctx.function.owner = name
        self.ctx.initializing = True
        assigned: dict[str, str] = {}
        self.ctx.ctor_assigned = assigned
        owner = name
        while owner != "" and self.decl.classes[owner].init_node.kind == "none":
            owner = self.decl.classes[owner].base
        terminated = False
        if owner != "":
            terminated = self.init_body(name, owner, self.decl.classes[owner].init_node.get("body"), assigned)
        if not terminated:
            self.construct(name, assigned, cls.node)
            self.ctx.function.body.append(BodyStmt("Return", cls.node).add(self.ctx.get("self", cls.node)))
        self.functions.append(self.ctx.function)

    def construct(self, name: str, assigned: dict[str, str], node: Node) -> None:
        if "self" in self.ctx.flow.assigned:
            return
        expr = self.construct_value(name, assigned, node)
        self.ctx.declare("self", expr.ty, node)
        self.ctx.flow.assigned["self"] = True
        self.ctx.function.body.append(BodyStmt("SetLocal", node, "self").add(expr))

    def construct_value(self, name: str, assigned: dict[str, str], node: Node) -> BodyExpr:
        cls = self.decl.classes[name]
        expr = BodyExpr("StructNew", DeclType("class", name), node, name)
        for field_name in cls.fields:
            field = cls.fields[field_name]
            if field_name in assigned:
                expr.add(self.ctx.get(assigned[field_name], node))
            elif field.default.kind != "none":
                expr.add(self.coerce(self.expr(field.default, field.ty), field.ty, field.default))
            else:
                declaration_error(name + ".__init__ must assign self." + field_name + " before self is used", node)
        return expr

    def init_body(self, name: str, owner: str, body: Node, assigned: dict[str, str]) -> bool:
        previous_owner = self.ctx.function.owner
        self.ctx.function.owner = owner
        bound_names(body, self.ctx.bound)
        self.prepare(body)
        terminated = False
        for position in range(len(body.items)):
            node = body.items[position]
            call = node.get("value") if node.kind == "Expr" else missing()
            func = call.get("func") if call.kind == "Call" else missing()
            receiver = func.get("value") if func.kind == "Attribute" else missing()
            if (func.kind == "Attribute" and func.get("attr").text == "__init__"
                    and receiver.kind == "Call" and named(receiver.get("func"), "super")):
                if "self" in self.ctx.flow.assigned or len(receiver.get("args").items) != 0 or len(receiver.get("keywords").items) != 0:
                    declaration_error("super().__init__ must precede self use and super() takes no arguments", node)
                base = self.decl.classes[owner].base
                while base != "" and self.decl.classes[base].init_node.kind == "none":
                    base = self.decl.classes[base].base
                if base == "":
                    self.arity(call, 0, 0)
                    if len(call.get("keywords").items) != 0:
                        declaration_error("object.__init__ takes no arguments", call)
                    continue
                signature = self.decl.classes[base].constructor
                bound = self.bind_call(base, signature, call)
                expr = bound
                if bound.kind == "Seq":
                    self.ctx.function.body.extend(bound.pre)
                    expr = bound.args[0]
                base_body = self.decl.classes[base].init_node.get("body")
                locals: list[str] = []
                bound_names(base_body, locals)
                for param in signature.params:
                    if param.name not in locals:
                        locals.append(param.name)
                mapping: dict[str, str] = {}
                prefix = "init." + str(self.ctx.temps) + "."
                self.ctx.temps += 1
                for local in locals:
                    mapping[local] = prefix + local
                for index in range(len(signature.params)):
                    param = signature.params[index]
                    local = mapping[param.name]
                    self.ctx.declare(local, param.ty, node)
                    self.ctx.flow.assigned[local] = True
                    self.ctx.function.body.append(BodyStmt("SetLocal", node, local).add(expr.args[index]))
                outer = self.ctx.function.body
                self.ctx.function.body = []
                self.ctx.inline_exits.append(False)
                ended = self.init_body(name, base, rename_body(base_body, mapping), assigned)
                exited = self.ctx.inline_exits.pop()
                inline = BodyStmt("InlineBlock", node)
                inline.body = self.ctx.function.body
                outer.append(inline)
                self.ctx.function.body = outer
                terminated = terminated or (ended and not exited)
                continue
            if "self" not in self.ctx.flow.assigned and node.kind in ("Assign", "AnnAssign"):
                target = node.get("target") if node.kind == "AnnAssign" else missing()
                if node.kind == "Assign" and len(node.get("targets").items) == 1:
                    target = node.get("targets").items[0]
                value_node = node.get("value")
                if (target.kind == "Attribute" and named(target.get("value"), "self")
                        and value_node.kind != "none" and not refers_self(value_node)):
                    field_name = target.get("attr").text
                    cls = self.decl.classes[name]
                    if field_name not in cls.fields or (field_name.startswith("__") and not self.decl.classes[owner].builtin):
                        declaration_error(name + " has no accessible field '" + field_name + "'", target)
                    field = cls.fields[field_name]
                    if node.kind == "AnnAssign":
                        declaration_error("field annotations belong in the class body", node)
                    value = self.coerce(self.expr(value_node, field.ty), field.ty, value_node)
                    temp = self.ctx.temp(field.ty, node)
                    self.ctx.function.body.append(BodyStmt("SetLocal", node, temp).add(value))
                    assigned[field_name] = temp
                    continue
            if refers_self(node):
                for later in body.items[position + 1:]:
                    if super_initializer(later).kind != "none":
                        declaration_error("super().__init__ must precede self use", node)
                self.construct(name, assigned, node)
            self.ctx.inline_return_ok = len(self.ctx.inline_exits) > 0
            for later in body.items[position + 1:]:
                if refers_self(later) or super_initializer(later).kind != "none":
                    self.ctx.inline_return_ok = False
            checked = self.statement(node)
            self.ctx.inline_return_ok = False
            self.ctx.function.body.extend(checked.stmts)
            terminated = terminated or checked.terminated
        self.ctx.function.owner = previous_owner
        return terminated

    def constructor_return(self, node: Node) -> BodyBlock:
        if node.get("value").kind != "none":
            declaration_error("__init__ cannot return a value", node)
        result = BodyBlock()
        result.terminated = True
        if len(self.ctx.inline_exits) > 0:
            if "self" not in self.ctx.flow.assigned and not self.ctx.inline_return_ok:
                declaration_error("this return in a base __init__ would skip code that uses self or assigns a field before the object exists", node)
            self.ctx.inline_exits[-1] = True
            result.stmts.append(BodyStmt("ExitInline", node))
            return result
        ty = self.ctx.function.sig.ret
        if "self" not in self.ctx.flow.assigned:
            expr = self.construct_value(ty.name, self.ctx.ctor_assigned, node)
            self.ctx.declare("self", ty, node)
            result.stmts.append(BodyStmt("SetLocal", node, "self").add(expr))
        value = BodyExpr("LocalGet", ty, node, "self")
        value.declared = ty
        result.stmts.append(BodyStmt("Return", node).add(value))
        return result

    def bind_call(self, name: str, sig: DeclSignature, node: Node, posonly: i32 = 0) -> BodyExpr:
        params = sig.params
        args = node.get("args").items
        if len(args) > len(params):
            declaration_error(name + "() has too many arguments", node)
        values: dict[str, BodyExpr] = {}
        pre: list[BodyStmt] = []
        for index in range(len(args)):
            param = params[index]
            user_type = inner_type(param.ty) if param.name.startswith("__") else param.ty
            value = self.coerce(self.expr(args[index], user_type), user_type, args[index])
            temp = self.ctx.temp(param.ty, node)
            pre.append(BodyStmt("SetLocal", node, temp).add(value))
            values[param.name] = self.ctx.get(temp, node)
        for kw in node.get("keywords").items:
            key = kw.get("arg")
            if key.kind == "none":
                body_unsupported("**kwargs", kw)
            if key.text in values:
                declaration_error(name + "() got multiple values for argument '" + key.text + "'", kw)
            found = False
            for index in range(len(params)):
                param = params[index]
                if param.name == key.text:
                    if index < posonly:
                        declaration_error(name + "() takes '" + key.text + "' as a positional argument only", kw)
                    found = True
                    value = self.coerce(self.expr(kw.get("value"), param.ty), param.ty, kw.get("value"))
                    temp = self.ctx.temp(param.ty, kw)
                    pre.append(BodyStmt("SetLocal", kw, temp).add(value))
                    values[param.name] = self.ctx.get(temp, kw)
            if not found or key.text.startswith("__"):
                declaration_error(name + "() got an unexpected keyword argument '" + key.text + "'", kw)
        result = BodyExpr("Call", sig.ret, node, name)
        for param in params:
            if param.name not in values:
                if param.default.kind == "none":
                    declaration_error(name + "() missing required argument '" + param.name + "'", node)
                values[param.name] = self.coerce(self.expr(param.default, param.ty), param.ty, param.default)
            result.add(values[param.name])
        if len(pre) == 0:
            return result
        seq = BodyExpr("Seq", sig.ret, node).add(result)
        seq.pre = pre
        return seq

    def block(self, nodes: list[Node]) -> BodyBlock:
        block = BodyBlock()
        for node in nodes:
            stmt = self.statement(node)
            block.stmts.extend(stmt.stmts)
            block.terminated = block.terminated or stmt.terminated
        return block

    def assign(self, target: Node, node: Node, annotation: DeclType) -> BodyStmt:
        if target.kind != "Name":
            if annotation.kind != "unknown":
                declaration_error("annotations on assignment targets require a local name", target)
            hint = self.target_hint(target)
            value = self.expr(node, hint)
            stmt = BodyStmt("If", target).add(BodyExpr("Const", DeclType("bool"), target, "true"))
            value = self.capture(value, stmt.body)
            stmt.body.extend(self.store(target, value))
            return stmt
        name = target.get("id").text
        if name == "self" and self.ctx.function.owner != "":
            declaration_error("cannot assign to self", target)
        if annotation.kind != "unknown":
            body_type(annotation, target)
            if annotation.kind == "None":
                declaration_error("a variable cannot have type None", target)
            self.ctx.declare(name, annotation, target)
        if node.kind == "none":
            return BodyStmt("Pass", target)
        hint = self.ctx.types.get(name, DeclType("unknown"))
        value = self.expr(node, hint)
        self.no_mixed(value, node)
        if hint.kind == "unknown":
            if value.ty.kind == "None":
                declaration_error("cannot infer a local type from None; annotate the optional reference", node)
            self.ctx.declare(name, value.ty, target)
        else:
            value = self.coerce(value, hint, node)
        self.ctx.flow.assigned[name] = True
        if self.ctx.types[name].reference() and not value.ty.same(self.ctx.types[name]) and value.ty.kind != "None":
            self.ctx.flow.narrowed[name] = value.ty
        elif name in self.ctx.flow.narrowed:
            del self.ctx.flow.narrowed[name]
        return BodyStmt("SetLocal", target, name).add(value)

    def target_hint(self, target: Node) -> DeclType:
        if target.kind == "Name":
            return self.ctx.types.get(target.get("id").text, DeclType("unknown"))
        if target.kind == "Attribute":
            obj = self.expr(target.get("value"), DeclType("unknown"))
            return self.field(obj, target.get("attr").text, target).ty
        if target.kind == "Subscript":
            obj = self.expr(target.get("value"), DeclType("unknown"))
            if obj.ty.kind == "list":
                return obj.ty.args[0]
            if obj.ty.kind == "dict":
                return obj.ty.args[1]
        if target.kind in ("Tuple", "List"):
            hint = DeclType("tuple")
            for child in target.get("elts").items:
                arg = self.target_hint(child)
                if arg.kind == "unknown":
                    return arg
                hint.add(arg)
            return hint
        return DeclType("unknown")

    def store(self, target: Node, value: BodyExpr) -> list[BodyStmt]:
        self.no_mixed(value, target)
        if target.kind == "Name":
            name = target.get("id").text
            if name == "self" and self.ctx.function.owner != "":
                declaration_error("cannot assign to self", target)
            if name in self.ctx.types:
                value = self.coerce(value, self.ctx.types[name], target)
            else:
                self.ctx.declare(name, value.ty, target)
            self.ctx.flow.assigned[name] = True
            if name in self.ctx.flow.narrowed:
                del self.ctx.flow.narrowed[name]
            return [BodyStmt("SetLocal", target, name).add(value)]
        if target.kind in ("Tuple", "List"):
            targets = target.get("elts").items
            if value.ty.kind != "tuple" or len(targets) != len(value.ty.args):
                declaration_error("unpacking needs a matching fixed-length tuple", target)
            out: list[BodyStmt] = []
            value = self.capture(value, out)
            for index in range(len(targets)):
                out.extend(self.store(targets[index], BodyExpr("TupleGet", value.ty.args[index], target, str(index)).add(value)))
            return out
        if target.kind == "Attribute":
            obj = self.expr(target.get("value"), DeclType("unknown"))
            field = self.field(obj, target.get("attr").text, target)
            if field.final:
                declaration_error("'" + field.name + "' is Final and can only be initialized before self is used", target)
            value = self.coerce(value, field.ty, value.node)
            return [BodyStmt("SetField", target, field.name).add(obj).add(value)]
        if target.kind == "Subscript":
            obj = self.expr(target.get("value"), DeclType("unknown"))
            index_node = target.get("slice")
            if index_node.kind == "Slice":
                body_unsupported("slice assignment", target)
            if obj.ty.kind == "dict":
                key = self.coerce(self.expr(index_node, obj.ty.args[0]), obj.ty.args[0], index_node)
                value = self.coerce(value, obj.ty.args[1], value.node)
                return [BodyStmt("ExprStmt", target).add(BodyExpr("DictOp", DeclType("None"), target, "set").add(obj).add(key).add(value))]
            if obj.ty.kind != "list":
                declaration_error("item assignment needs a mutable list or dictionary", target)
            value = self.coerce(value, obj.ty.args[0], value.node)
            return [BodyStmt("SetIndex", target).add(obj).add(self.index(index_node)).add(value)]
        body_unsupported("assignment target " + target.kind, target)
        return []

    def augmented(self, node: Node) -> BodyBlock:
        target = node.get("target")
        result = BodyBlock()
        old = self.expr(target, DeclType("unknown"))
        if old.kind in ("GetField", "ListGet", "DictOp"):
            for index in range(len(old.args)):
                old.args[index] = self.capture(old.args[index], result.stmts)
        elif old.kind not in ("LocalGet", "CapturedGet"):
            declaration_error("unsupported augmented assignment target", target)
        if old.kind == "GetField" and self.field(old.args[0], old.text, target).final:
            declaration_error("'" + old.text + "' is Final", target)
        if target.kind == "Name" and target.get("id").text == "self" and self.ctx.function.owner != "":
            declaration_error("cannot assign to self", target)
        current = self.capture(old, result.stmts)
        rhs = node.get("value")
        op = node.get("op").kind
        if old.ty.kind == "list":
            if op not in ("Add", "Mult"):
                declaration_error("unsupported augmented list operator", node)
            expected = old.ty if op == "Add" else DeclType("i32")
            value = self.coerce(self.expr(rhs, expected), expected, rhs)
            result.stmts.append(BodyStmt("ExprStmt", node).add(BodyExpr("ListOp", DeclType("None"), node,
                                "extend" if op == "Add" else "imul").add(current).add(value)))
            value = current
        else:
            value = self.binary(op, current, self.expr(rhs, old.ty if number_node(rhs) else DeclType("unknown")), node)
            value = self.coerce(value, old.ty, node)
        if old.kind in ("LocalGet", "CapturedGet"):
            value = self.coerce(value, self.ctx.types[old.text], node)
            result.stmts.append(BodyStmt("SetLocal", node, old.text).add(value))
            if old.text in self.ctx.flow.narrowed:
                del self.ctx.flow.narrowed[old.text]
        elif old.kind == "GetField":
            result.stmts.append(BodyStmt("SetField", node, old.text).add(old.args[0]).add(value))
        elif old.kind == "ListGet":
            result.stmts.append(BodyStmt("SetIndex", node).add(old.args[0]).add(old.args[1]).add(value))
        else:
            result.stmts.append(BodyStmt("ExprStmt", node).add(BodyExpr("DictOp", DeclType("None"), node, "set")
                                .add(old.args[0]).add(old.args[1]).add(value)))
        return result

    def statement(self, node: Node) -> BodyBlock:
        result = BodyBlock()
        if node.kind in ("FunctionDef", "AsyncFunctionDef"):
            if self.ctx.initializing:
                declaration_error("closures in __init__ are not supported yet; use an ordinary method", node)
            sig = self.ctx.nested_sigs[str(node.line) + ":" + str(node.column)]
            target = Node("Name", "", node.line, node.column).put("id", node.get("name"))
            result.stmts = self.store(target, self.defer_closure(node, sig))
            return result
        if node.kind == "Nonlocal":
            return result
        if docstring(node) or node.kind == "Pass":
            return result
        if node.kind == "Assign":
            targets = node.get("targets").items
            if len(targets) != 1:
                declaration_error("chained assignment is not supported", node)
            result.stmts.append(self.assign(targets[0], node.get("value"), DeclType("unknown")))
        elif node.kind == "AnnAssign":
            result.stmts.append(self.assign(node.get("target"), node.get("value"), self.annotation(node.get("annotation"))))
        elif node.kind == "AugAssign":
            return self.augmented(node)
        elif node.kind == "Delete":
            for target in node.get("targets").items:
                if target.kind != "Subscript" or target.get("slice").kind == "Slice":
                    body_unsupported("deletion except dictionary keys", target)
                obj = self.expr(target.get("value"), DeclType("unknown"))
                if obj.ty.kind != "dict":
                    body_unsupported("deletion except dictionary keys", target)
                key = self.coerce(self.expr(target.get("slice"), obj.ty.args[0]), obj.ty.args[0], target.get("slice"))
                result.stmts.append(BodyStmt("ExprStmt", node).add(BodyExpr("DictOp", DeclType("None"), node, "delete").add(obj).add(key)))
        elif node.kind == "Expr":
            expr = node.get("value")
            if expr.kind == "Yield":
                if self.ctx.yield_type.kind == "None" or expr.get("value").kind == "none":
                    declaration_error("yield needs a value in a typed generator", expr)
                yielded = expr.get("value")
                result.stmts.append(BodyStmt("Yield", node).add(self.coerce(self.expr(yielded, self.ctx.yield_type), self.ctx.yield_type, yielded)))
                return result
            if expr.kind == "Call" and named(expr.get("func"), "print") and "print" not in self.ctx.bound:
                return self.print_statement(expr)
            value = self.expr(expr, DeclType("unknown"))
            if value.kind != "NoneLit":
                result.stmts.append(BodyStmt("ExprStmt", node).add(value))
        elif node.kind == "Return":
            if len(self.ctx.finally_loops) > 0:
                declaration_error("return in a finally block would swallow an in-flight exception", node)
            if self.ctx.initializing:
                return self.constructor_return(node)
            if self.ctx.function.name == "snek.main":
                declaration_error("return outside a function", node)
            value_node = node.get("value")
            ret = BodyStmt("Return", node)
            if self.ctx.result_type.kind == "None":
                if value_node.kind != "none" and not none_node(value_node):
                    declaration_error("function declared -> None cannot return a value", node)
            else:
                if value_node.kind == "none":
                    declaration_error("function must return a " + self.ctx.result_type.display(), node)
                ret.add(self.coerce(self.expr(value_node, self.ctx.result_type), self.ctx.result_type, value_node))
            result.stmts.append(ret)
            result.terminated = True
        elif node.kind == "If":
            stmt = BodyStmt("If", node).add(self.condition(node.get("test")))
            before = self.ctx.flow.copy()
            self.facts(node.get("test"), True, self.ctx.flow.narrowed)
            then = self.block(node.get("body").items)
            then_flow = self.ctx.flow
            self.ctx.flow = before.copy()
            self.facts(node.get("test"), False, self.ctx.flow.narrowed)
            otherwise = self.block(node.get("orelse").items)
            if then.terminated and otherwise.terminated:
                self.ctx.flow = before
            elif otherwise.terminated:
                self.ctx.flow = then_flow
            elif not then.terminated:
                self.ctx.flow = then_flow.merge(self.ctx.flow)
            stmt.body = then.stmts
            stmt.otherwise = otherwise.stmts
            result.stmts.append(stmt)
            result.terminated = then.terminated and otherwise.terminated
        elif node.kind == "While":
            if len(node.get("orelse").items) != 0:
                declaration_error("while/else is not supported", node)
            self.enter_loop(node.get("body"))
            before = self.ctx.flow.copy()
            condition = self.condition(node.get("test"))
            self.facts(node.get("test"), True, self.ctx.flow.narrowed)
            self.ctx.loop_depth += 1
            stmt = BodyStmt("Loop", node)
            stmt.body = self.block(node.get("body").items).stmts
            self.ctx.loop_depth -= 1
            self.ctx.flow = before
            infinite = condition.kind == "Const" and condition.ty.kind == "bool" and condition.text == "true"
            if not infinite:
                stmt.add(condition)
            result.stmts.append(stmt)
            result.terminated = infinite and not direct_break(node.get("body"))
        elif node.kind == "For":
            it = node.get("iter")
            if it.kind == "Call" and named(it.get("func"), "range") and "range" not in self.ctx.bound:
                return self.range_loop(node)
            return self.collection_loop(node)
        elif node.kind in ("Break", "Continue"):
            if self.ctx.loop_depth == 0:
                declaration_error(node.kind.lower() + " outside a loop", node)
            if len(self.ctx.finally_loops) > 0 and self.ctx.loop_depth <= self.ctx.finally_loops[-1]:
                declaration_error(node.kind.lower() + " cannot exit a finally block", node)
            result.stmts.append(BodyStmt(node.kind, node))
            result.terminated = True
        elif node.kind == "Assert":
            cond = self.condition(node.get("test"))
            msg = node.get("msg")
            message = BodyExpr("NoneLit", DeclType("optional").add(DeclType("str")), node)
            if msg.kind != "none":
                message = self.as_string(self.expr(msg, DeclType("unknown")), msg)
            exc = BodyExpr("New", DeclType("class", "AssertionError"), node, "AssertionError").add(message)
            stmt = BodyStmt("If", node).add(BodyExpr("Unary", DeclType("bool"), node, "not").add(cond))
            stmt.body.append(BodyStmt("Raise", node).add(exc))
            result.stmts.append(stmt)
            self.facts(node.get("test"), True, self.ctx.flow.narrowed)
        elif node.kind == "Raise":
            exc_node = node.get("exc")
            if node.get("cause").kind != "none":
                body_unsupported("exception chaining (raise from)", node)
            if exc_node.kind == "none":
                if len(self.ctx.handler_excs) == 0:
                    declaration_error("bare raise is only allowed inside an except block", node)
                local = self.ctx.handler_excs[-1]
                if local == "":
                    declaration_error("bare raise in a finally block is not supported", node)
                result.stmts.append(BodyStmt("Reraise", node, local))
            else:
                if exc_node.kind == "Name" and exc_node.get("id").text in self.decl.classes and exc_node.get("id").text not in self.ctx.bound:
                    exc_node = Node("Call", "", node.line, node.column).put("func", exc_node).put("args", sequence([])).put("keywords", sequence([]))
                value = self.expr(exc_node, DeclType("unknown"))
                if value.ty.kind != "class" or not self.subclass(value.ty.name, "BaseException"):
                    declaration_error("raise needs a non-optional exception instance derived from BaseException", node)
                result.stmts.append(BodyStmt("Raise", node).add(value))
            result.terminated = True
        elif node.kind == "Try":
            return self.try_statement(node)
        elif node.kind == "With":
            return self.with_statement(node, 0)
        else:
            body_unsupported("statement " + node.kind, node)
        return result

    def enter_loop(self, body: Node) -> None:
        self.ctx.flow.forget_narrowings(body)
        self.ctx.flow.forget_handlers(body)

    def with_statement(self, node: Node, index: i32) -> BodyBlock:
        item = node.get("items").items[index]
        manager = self.expr(item.get("context_expr"), DeclType("unknown"))
        if manager.ty.kind == "Stream":
            result = BodyBlock()
            recv = self.capture(manager, result.stmts)
            result.stmts.append(BodyStmt("ExprStmt", node).add(BodyExpr("Call", DeclType("None"), node, "snek.sys.ensure_open").add(recv)))
            bind: list[BodyStmt] = []
            if item.get("optional_vars").kind != "none":
                bind = self.store(item.get("optional_vars"), recv)
            body = self.with_statement(node, index + 1) if index + 1 < len(node.get("items").items) else self.block(node.get("body").items)
            exc = self.ctx.temp(optional_type(DeclType("class", "BaseException")), node)
            stmt = BodyStmt("Try", node, exc)
            stmt.body = bind + body.stmts
            stmt.has_final = True
            stmt.finalbody = [BodyStmt("ExprStmt", node).add(BodyExpr("Call", DeclType("None"), node, "snek.sys.stream_close").add(recv))]
            result.stmts.append(stmt)
            result.terminated = body.terminated
            return result
        if manager.ty.kind != "class":
            declaration_error("with needs a non-optional class instance", item)
        cls = self.decl.classes[manager.ty.name]
        if "__enter__" not in cls.methods or "__exit__" not in cls.methods:
            declaration_error(cls.name + " needs both __enter__ and __exit__ for with", node)
        enter = cls.methods["__enter__"].sig
        exit_sig = cls.methods["__exit__"].sig
        result = BodyBlock()
        recv = self.capture(manager, result.stmts)
        handled = self.ctx.temp(DeclType("bool"), node)
        exc = self.ctx.temp(optional_type(DeclType("class", "BaseException")), node)
        result.stmts.append(BodyStmt("SetLocal", node, handled).add(BodyExpr("Const", DeclType("bool"), node, "false")))
        entered = BodyExpr("CallMethod", enter.ret, node, "__enter__").add(recv)
        target = item.get("optional_vars")
        bind: list[BodyStmt] = []
        before = self.ctx.flow.copy()
        if target.kind != "none":
            if enter.ret.kind == "None":
                declaration_error("with ... as needs a non-None __enter__ result", target)
            value = self.capture(entered, result.stmts)
            before = self.ctx.flow.copy()
            bind = self.store(target, value)
            if target.kind == "Name":
                before = self.ctx.flow.copy()
        else:
            result.stmts.append(BodyStmt("ExprStmt", node).add(entered))
        body = self.with_statement(node, index + 1) if index + 1 < len(node.get("items").items) else self.block(node.get("body").items)
        if exit_sig.ret.kind == "bool":
            before.forget_narrowings(node)
            before.forget_handlers(node)
            self.ctx.flow = before if body.terminated else before.merge(self.ctx.flow)
            body.terminated = False
        caught = BodyExpr("LocalGet", DeclType("class", "BaseException"), node, exc)
        caught.declared = self.ctx.types[exc]
        error_exit = BodyExpr("CallMethod", exit_sig.ret, node, "__exit__").add(recv)
        error_exit.add(BodyExpr("TypeOf", DeclType("type").add(caught.ty), node).add(caught))
        error_exit.add(caught).add(BodyExpr("TracebackToken", DeclType("TracebackType"), node))
        handler = BodyHandler()
        handler.catches_all = True
        handler.body.append(BodyStmt("SetLocal", node, handled).add(BodyExpr("Const", DeclType("bool"), node, "true")))
        if exit_sig.ret.kind == "bool":
            test = BodyStmt("If", node).add(BodyExpr("Unary", DeclType("bool"), node, "not").add(error_exit))
            test.body.append(BodyStmt("Reraise", node, exc))
            handler.body.append(test)
        else:
            handler.body.append(BodyStmt("ExprStmt", node).add(error_exit))
            handler.body.append(BodyStmt("Reraise", node, exc))
        normal_exit = BodyExpr("CallMethod", exit_sig.ret, node, "__exit__").add(recv)
        for param in exit_sig.params:
            normal_exit.add(BodyExpr("NoneLit", param.ty, node))
        cleanup = BodyStmt("If", node).add(BodyExpr("Unary", DeclType("bool"), node, "not").add(self.ctx.get(handled, node)))
        cleanup.body.append(BodyStmt("ExprStmt", node).add(normal_exit))
        stmt = BodyStmt("Try", node, exc)
        stmt.body = bind + body.stmts
        stmt.handlers = [handler]
        stmt.finalbody = [cleanup]
        stmt.has_final = True
        result.stmts.append(stmt)
        result.terminated = body.terminated
        return result

    def try_statement(self, node: Node) -> BodyBlock:
        before = self.ctx.flow.copy()
        local = self.ctx.temp(optional_type(DeclType("class", "BaseException")), node)
        stmt = BodyStmt("Try", node, local)
        self.ctx.flow = before.copy()
        body = self.block(node.get("body").items)
        otherwise = self.block(node.get("orelse").items)
        stmt.body = body.stmts
        stmt.otherwise = otherwise.stmts
        all_terminated = body.terminated or otherwise.terminated
        exits: list[BodyFlow] = []
        if not all_terminated:
            exits.append(self.ctx.flow)
        handlers = node.get("handlers").items
        for index in range(len(handlers)):
            handler = handlers[index]
            item = BodyHandler()
            types = handler.get("type")
            item.catches_all = types.kind == "none"
            caught = DeclType("class", "BaseException")
            if item.catches_all:
                if index != len(handlers) - 1:
                    declaration_error("a bare except must be the last handler", handler)
            else:
                nodes = types.get("elts").items if types.kind == "Tuple" else [types]
                for cls_node in nodes:
                    if cls_node.kind != "Name" or cls_node.get("id").text not in self.decl.classes:
                        declaration_error("except needs an exception class or tuple of classes", cls_node)
                    name = cls_node.get("id").text
                    if not self.subclass(name, "BaseException"):
                        declaration_error("except needs a class derived from BaseException", cls_node)
                    ty = DeclType("class", name)
                    caught = ty if len(item.classes) == 0 else self.join(caught, ty)
                    item.classes.append(name)
            self.ctx.flow = before.copy()
            self.ctx.flow.forget_narrowings(node.get("body"))
            self.ctx.flow.forget_handlers(node.get("body"))
            var = handler.get("name")
            if var.kind != "none":
                item.name = var.text
                item.declared = self.ctx.types.get(var.text, DeclType("class", "BaseException"))
                if inner_type(item.declared).kind != "class" or not self.assignable(caught, item.declared):
                    declaration_error("exception target already has an incompatible local type", handler)
                self.ctx.declare(var.text, item.declared, handler)
                self.ctx.flow.assigned[var.text] = True
                if not caught.same(item.declared):
                    self.ctx.flow.narrowed[var.text] = caught
                elif var.text in self.ctx.flow.narrowed:
                    del self.ctx.flow.narrowed[var.text]
            self.ctx.handler_excs.append(local)
            checked = self.block(handler.get("body").items)
            self.ctx.handler_excs.pop()
            item.body = checked.stmts
            self.ctx.flow.forget_handlers(handler)
            stmt.handlers.append(item)
            if not checked.terminated:
                exits.append(self.ctx.flow)
            all_terminated = all_terminated and checked.terminated
        merged = before.copy()
        if len(exits) > 0:
            merged = exits[0]
            for index in range(1, len(exits)):
                merged = merged.merge(exits[index])
        final_terminated = False
        final = node.get("finalbody")
        if len(final.items) > 0:
            stmt.has_final = True
            self.ctx.flow = before.copy()
            region = sequence([node.get("body"), node.get("orelse"), node.get("handlers")])
            self.ctx.flow.forget_narrowings(region)
            self.ctx.flow.forget_handlers(region)
            self.ctx.finally_loops.append(self.ctx.loop_depth)
            self.ctx.handler_excs.append("")
            checked = self.block(final.items)
            self.ctx.handler_excs.pop()
            self.ctx.finally_loops.pop()
            stmt.finalbody = checked.stmts
            final_terminated = checked.terminated
            merged.forget_handlers(final)
            for name in self.ctx.flow.assigned:
                merged.assigned[name] = True
            names: list[str] = []
            bound_names(final, names)
            for name in names:
                if name in merged.narrowed:
                    del merged.narrowed[name]
                if name in self.ctx.flow.narrowed:
                    merged.narrowed[name] = self.ctx.flow.narrowed[name]
        result = BodyBlock()
        result.terminated = all_terminated or final_terminated
        self.ctx.flow = before.copy() if result.terminated else merged
        result.stmts.append(stmt)
        return result

    def collection_loop(self, node: Node) -> BodyBlock:
        if len(node.get("orelse").items) != 0:
            declaration_error("for/else is not supported", node)
        self.enter_loop(node)
        before = self.ctx.flow.copy()
        seq = self.expr(node.get("iter"), DeclType("unknown"))
        ty = seq.ty
        if ty.kind == "Iterator":
            result = BodyBlock()
            seq = self.capture(seq, result.stmts)
            loop = BodyStmt("Loop", node).add(BodyExpr("MachineOp", DeclType("bool"), node, "advance").add(seq))
            loop.body = self.store(node.get("target"), BodyExpr("MachineOp", ty.args[0], node, "value").add(seq))
            self.ctx.loop_depth += 1
            loop.body.extend(self.block(node.get("body").items).stmts)
            self.ctx.loop_depth -= 1
            self.ctx.flow = before
            result.stmts.append(loop)
            return result
        if ty.kind not in ("list", "tuple", "str", "dict", "view"):
            body_unsupported("iteration over " + ty.display(), node)
        result = BodyBlock()
        view = "keys"
        if ty.kind == "view":
            view = ty.name
            seq = BodyExpr("DictOp", ty.args[0], node, "mapping").add(seq)
            ty = ty.args[0]
        seq = self.capture(seq, result.stmts)
        index = self.capture(self.integer(0, node), result.stmts)
        loop = BodyStmt("Loop", node)
        if ty.kind == "dict":
            count = self.capture(BodyExpr("DictOp", DeclType("i32"), node, "iter_start").add(seq), result.stmts)
            remaining = self.capture(count, result.stmts)
            next = BodyExpr("DictOp", DeclType("i32"), node, "next").add(seq).add(index).add(count).add(remaining)
            cond = BodyExpr("Compare", DeclType("bool"), node, "ge").add(index).add(self.integer(0, node))
            loop.add(self.sequence_expr([BodyStmt("SetLocal", node, index.text).add(next)], cond))
            elem = self.view_element(DeclType("view", view).add(ty))
            value = BodyExpr("DictOp", elem, node, view + "_at").add(seq).add(index)
            loop.body.extend(self.store(node.get("target"), value))
            loop.body.append(BodyStmt("SetLocal", node, remaining.text)
                             .add(BodyExpr("Binary", DeclType("i32"), node, "sub").add(remaining).add(self.integer(1, node))))
        else:
            if ty.kind == "str":
                if node.get("target").kind != "Name":
                    declaration_error("string loop target must be a name", node)
                value = BodyExpr("Call", ty, node, "rt.char_at").add(seq).add(index)
                length = BodyExpr("Call", DeclType("i32"), node, "rt.blen").add(seq)
            elif ty.kind == "tuple":
                value = BodyExpr("TupleOp", self.homogeneous(ty, node), node, "get").add(seq).add(index)
                length = self.integer(len(ty.args), node)
            else:
                value = BodyExpr("ListGet", ty.args[0], node).add(seq).add(index)
                length = self.length(seq, node)
            loop.body.extend(self.store(node.get("target"), value))
            loop.add(BodyExpr("Compare", DeclType("bool"), node, "lt").add(index).add(length))
        self.ctx.loop_depth += 1
        loop.body.extend(self.block(node.get("body").items).stmts)
        self.ctx.loop_depth -= 1
        step = (BodyExpr("Call", DeclType("i32"), node, "rt.wat").add(seq).add(index)
                if ty.kind == "str" else self.integer(1, node))
        loop.step.append(BodyStmt("SetLocal", node, index.text).add(BodyExpr("Binary", DeclType("i32"), node, "add").add(index).add(step)))
        if ty.kind == "dict":
            exc = self.ctx.temp(DeclType("class", "BaseException"), node)
            cleanup = BodyStmt("TryFinally", node, exc)
            cleanup.body.append(loop)
            cleanup.otherwise.append(BodyStmt("ExprStmt", node).add(BodyExpr("DictOp", DeclType("None"), node, "iter_end").add(seq)))
            result.stmts.append(cleanup)
        else:
            result.stmts.append(loop)
        self.ctx.flow = before
        return result

    def range_loop(self, node: Node) -> BodyBlock:
        it = node.get("iter")
        if (it.kind != "Call" or not named(it.get("func"), "range") or "range" in self.ctx.bound
                or node.get("target").kind != "Name"):
            body_unsupported("for loops except range() with a local target", node)
        if len(node.get("orelse").items) != 0 or len(it.get("keywords").items) != 0:
            body_unsupported("for/else or range keywords", node)
        args = it.get("args").items
        if len(args) < 1 or len(args) > 3:
            declaration_error("range needs one to three arguments", it)
        self.enter_loop(node)
        before = self.ctx.flow.copy()
        step: i64 = 1
        if len(args) == 3:
            if not integer_node(args[2]):
                body_unsupported("non-literal range step", args[2])
            step = i64(self.number(args[2], DeclType("i64")).text)
            if step == 0:
                declaration_error("range step cannot be zero", args[2])
        target = node.get("target").get("id").text
        hint = self.ctx.types.get(target, DeclType("unknown"))
        inferred: dict[i32, BodyExpr] = {}
        for index in range(min(len(args), 2)):
            if not integer_node(args[index]):
                value = self.expr(args[index], hint)
                inferred[index] = value
                if target not in self.ctx.types:
                    hint = value.ty if hint.kind == "unknown" else numeric_join(hint, value.ty)
        if hint.kind == "unknown" and len(inferred) == 0:
            hint = DeclType("i32")
        if not int_type(hint):
            declaration_error("range bounds and variable must have integer type", node)
        bounds: list[BodyExpr] = []
        for index in range(min(len(args), 2)):
            value = inferred[index] if index in inferred else self.expr(args[index], hint)
            if not int_type(value.ty):
                declaration_error("range bounds must be integers", args[index])
            bounds.append(value)
        if not int_type(hint):
            declaration_error("range variable must have integer type", node)
        if hint.kind == "i32" and (step < -2147483648 or step > 2147483647):
            declaration_error("range step does not fit in i32", it)
        start = BodyExpr("Const", hint, node, "0") if len(args) == 1 else bounds[0]
        stop = bounds[0] if len(args) == 1 else bounds[1]
        self.ctx.declare(target, hint, node)
        counter = self.ctx.temp(hint, node)
        end = self.ctx.temp(hint, node)
        result = BodyBlock()
        result.stmts.append(BodyStmt("SetLocal", node, counter).add(self.coerce(start, hint, node)))
        result.stmts.append(BodyStmt("SetLocal", node, end).add(self.coerce(stop, hint, node)))
        self.ctx.flow.assigned[target] = True
        loop = BodyStmt("Loop", node).add(BodyExpr("Compare", DeclType("bool"), node, "lt" if step > 0 else "gt")
                                            .add(self.ctx.get(counter, node)).add(self.ctx.get(end, node)))
        loop.body.append(BodyStmt("SetLocal", node, target).add(self.ctx.get(counter, node)))
        self.ctx.loop_depth += 1
        loop.body.extend(self.block(node.get("body").items).stmts)
        self.ctx.loop_depth -= 1
        loop.step.append(BodyStmt("SetLocal", node, counter).add(BodyExpr("Binary", hint, node, "add")
                                                               .add(self.ctx.get(counter, node)).add(BodyExpr("Const", hint, node, str(step)))))
        result.stmts.append(loop)
        self.ctx.flow = before
        return result

    def print_statement(self, node: Node) -> BodyBlock:
        result = BodyBlock()
        stmt = BodyStmt("Print", node)
        seen: list[str] = []
        for kw in node.get("keywords").items:
            name = kw.get("arg").text
            keyword_value = kw.get("value")
            if name not in ("sep", "end") or name in seen or keyword_value.kind != "Constant" or keyword_value.get("value").kind != "str":
                declaration_error("print accepts unique literal string sep/end keywords", kw)
            seen.append(name)
            if name == "sep":
                stmt.sep = keyword_value.get("value").text
            else:
                stmt.end = keyword_value.get("value").text
        for arg in node.get("args").items:
            value = self.expr(arg, DeclType("unknown"))
            self.no_mixed(value, arg)
            if value.ty.kind == "None":
                if value.kind != "NoneLit":
                    result.stmts.append(BodyStmt("ExprStmt", arg).add(value))
                stmt.add(BodyExpr("StrLit", DeclType("str"), arg, "None"))
            else:
                temp = self.ctx.temp(value.ty, arg)
                result.stmts.append(BodyStmt("SetLocal", arg, temp).add(value))
                stmt.add(self.as_string(self.ctx.get(temp, arg), arg))
        result.stmts.append(stmt)
        return result
