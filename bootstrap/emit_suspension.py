"""Connect owned typed bodies to owned suspension lowering, entirely in Snek."""
from __future__ import annotations
from snek import i32
from bootstrap.parser import Node, missing, scalar, sequence, string
from bootstrap.declarations import DeclType, DeclParam, DeclSignature
from bootstrap.bodies import BodyExpr, BodyStmt, BodyFunction
from bootstrap.emit_program import SourceEmitter
from bootstrap.machine_lower import MachineGraph, machine_has_await
from bootstrap.machine_emit import machine_frame_wat, machine_types_wat, machine_helpers_wat


def owned_flag(value: bool) -> Node:
    return Node("scalar", "true" if value else "false")


def owned_wasm_type(e: SourceEmitter, ty: DeclType) -> Node:
    return (Node("WasmType").put("none", owned_flag(ty.kind == "None"))
            .put("ref", owned_flag(ty.reference())).put("optional", owned_flag(ty.kind == "optional"))
            .put("heap", string(e.types.heap(ty) if ty.reference() else ""))
            .put("storage", string(e.types.storage(ty) if ty.kind != "None" else ""))
            .put("value", string(e.types.valtype(ty) if ty.kind != "None" else "")))


def owned_machine_type(e: SourceEmitter, ty: DeclType) -> Node:
    return Node("MachineType").put("heap", string(e.types.heap(ty))).put("elem", owned_wasm_type(e, ty.args[0])).put("generator", owned_flag(ty.kind == "Iterator"))


def owned_machine_types(e: SourceEmitter) -> str:
    machines: list[Node] = []
    for ty in list(e.types.machines.values()): machines.append(owned_machine_type(e, ty))
    return machine_types_wat(Node("MachineTypesRequest").put("machines", sequence(machines)).put("frames", sequence(e.frames)))


def owned_machine_helpers(e: SourceEmitter) -> str:
    machines: list[Node] = []
    for ty in list(e.types.machines.values()):
        machine = owned_machine_type(e, ty)
        generator = ty.kind == "Iterator"
        machine.put("busy_message", string(e.literals.lit("generator already executing" if generator else "coroutine already executing")))
        if generator: machine.put("close_message", string(e.literals.lit("generator ignored GeneratorExit")))
        else: machine.put("reuse_message", string(e.literals.lit("cannot reuse already awaited coroutine")))
        machines.append(machine)
    return machine_helpers_wat(Node("MachineHelpersRequest").put("machines", sequence(machines)))


class OwnedMachine:
    emitter: SourceEmitter
    types: list[DeclType]
    expressions: list[BodyExpr]
    statements: list[BodyStmt]

    def __init__(self, emitter: SourceEmitter) -> None:
        self.emitter = emitter
        self.types = []
        self.expressions = []
        self.statements = []

    def type(self, ty: DeclType) -> Node:
        for i in range(len(self.types)):
            if self.types[i].same(ty): return Node("Opaque").put("id", scalar(i))
        self.types.append(ty)
        return Node("Opaque").put("id", scalar(len(self.types) - 1))

    def untype(self, node: Node) -> DeclType:
        return self.types[i32(node.get("id").text)]

    def expression(self, expr: BodyExpr) -> Node:
        index = len(self.expressions)
        self.expressions.append(expr)
        result = Node(expr.kind).put("ty", self.type(expr.ty)).put("owned", scalar(index))
        if expr.kind == "Await": return result.put("operand", self.expression(expr.args[0]))
        if expr.kind == "Seq": return result.put("stmts", self.block(expr.pre)).put("value", self.expression(expr.args[0]))
        if expr.kind == "Logical":
            return result.put("op", string(expr.text)).put("left", self.expression(expr.args[0])).put("right", self.expression(expr.args[1]))
        if expr.kind == "IfExpr":
            return result.put("cond", self.expression(expr.args[0])).put("then", self.expression(expr.args[1])).put("else_", self.expression(expr.args[2]))
        args: list[Node] = []
        for arg in expr.args: args.append(self.expression(arg))
        return result.put("args", sequence(args))

    def unexpression(self, node: Node) -> BodyExpr:
        ty = self.untype(node.get("ty"))
        if "owned" in node.keys:
            original = self.expressions[i32(node.get("owned").text)]
            result = BodyExpr(original.kind, ty, original.node, original.text)
            result.declared = original.declared
            result.mixed = original.mixed
            if node.kind == "Seq":
                result.pre = self.unblock(node.get("stmts"))
                result.add(self.unexpression(node.get("value")))
            elif node.kind == "Logical":
                result.add(self.unexpression(node.get("left"))).add(self.unexpression(node.get("right")))
            elif node.kind == "IfExpr":
                result.add(self.unexpression(node.get("cond"))).add(self.unexpression(node.get("then"))).add(self.unexpression(node.get("else_")))
            else:
                for arg in node.get("args").items: result.add(self.unexpression(arg))
            return result
        if node.kind == "LocalGet":
            result = BodyExpr("LocalGet", ty, missing(), node.get("name").text)
            result.declared = self.untype(node.get("declared"))
            return result
        if node.kind == "Const": return BodyExpr("Const", ty, missing(), node.get("value").text)
        if node.kind == "Call": return BodyExpr("Call", ty, missing(), node.get("func").text)
        if node.kind == "MachineOp": return BodyExpr("MachineOp", ty, missing(), node.get("op").text).add(self.unexpression(node.get("obj")))
        if node.kind == "IsInstance": return BodyExpr("IsInstance", ty, missing(), node.get("cls").text).add(self.unexpression(node.get("operand")))
        raise ValueError("unknown owned machine expression " + node.kind)

    def block(self, body: list[BodyStmt]) -> Node:
        out: list[Node] = []
        for stmt in body: out.append(self.statement(stmt))
        return sequence(out)

    def unblock(self, body: Node) -> list[BodyStmt]:
        out: list[BodyStmt] = []
        for stmt in body.items: out.append(self.unstatement(stmt))
        return out

    def statement(self, stmt: BodyStmt) -> Node:
        index = len(self.statements)
        self.statements.append(stmt)
        result = Node(stmt.kind).put("owned", scalar(index))
        if stmt.kind == "If":
            return result.put("cond", self.expression(stmt.exprs[0])).put("then", self.block(stmt.body)).put("else_", self.block(stmt.otherwise))
        if stmt.kind == "Loop":
            cond = self.expression(stmt.exprs[0]) if len(stmt.exprs) != 0 else missing()
            return result.put("cond", cond).put("body", self.block(stmt.body)).put("step", self.block(stmt.step))
        if stmt.kind in ("Try", "TryFinally"):
            handlers: list[Node] = []
            for handler in stmt.handlers:
                classes: list[Node] = []
                for name in handler.classes: classes.append(string(name))
                handlers.append(Node("Handler").put("classes", missing() if handler.catches_all else sequence(classes))
                                .put("var", string(handler.name) if handler.name != "" else missing())
                                .put("var_decl", self.type(handler.declared)).put("body", self.block(handler.body)))
            return (Node("Try").put("owned", scalar(index)).put("exc_local", string(stmt.name)).put("body", self.block(stmt.body))
                    .put("handlers", sequence(handlers)).put("else_", self.block(stmt.otherwise if stmt.kind == "Try" else []))
                    .put("finally_", self.block(stmt.finalbody if stmt.kind == "Try" else stmt.otherwise)))
        if stmt.kind in ("Return", "Yield", "SetLocal"):
            return result.put("name", string(stmt.name)).put("value", self.expression(stmt.exprs[0]) if len(stmt.exprs) != 0 else missing())
        if stmt.kind == "ExprStmt": return result.put("expr", self.expression(stmt.exprs[0]))
        args: list[Node] = []
        for arg in stmt.exprs: args.append(self.expression(arg))
        return result.put("exprs", sequence(args)).put("body", self.block(stmt.body))

    def unstatement(self, node: Node) -> BodyStmt:
        if "owned" in node.keys:
            original = self.statements[i32(node.get("owned").text)]
            result = BodyStmt(original.kind, original.node, original.name)
            result.sep = original.sep
            result.end = original.end
            result.body = self.unblock(node.get("body")) if "body" in node.keys else original.body
        else: result = BodyStmt(node.kind, missing(), node.get("name").text if "name" in node.keys else "")
        if node.kind == "Try": return self.statements[i32(node.get("owned").text)]
        if node.kind == "If":
            result.add(self.unexpression(node.get("cond")))
            result.body = self.unblock(node.get("then"))
            result.otherwise = self.unblock(node.get("else_"))
        elif node.kind == "Loop":
            if node.get("cond").kind != "none": result.add(self.unexpression(node.get("cond")))
            result.step = self.unblock(node.get("step"))
        elif node.kind in ("SetLocal", "Return", "Yield"):
            if node.get("value").kind != "none": result.add(self.unexpression(node.get("value")))
        elif node.kind == "ExprStmt": result.add(self.unexpression(node.get("expr")))
        else:
            for arg in node.get("exprs").items: result.add(self.unexpression(arg))
        return result

    def run(self, f: BodyFunction) -> str:
        e = self.emitter
        params = f.sig.params.copy()
        locals_ = f.locals.copy()
        signature = ""
        cast = ""
        if f.closure_type.kind != "None":
            signature = e.types.heap(f.closure_type)[1:] + ".sig"
            params.insert(0, DeclParam("closure.env", DeclType("Env"), missing()))
        if f.method_decl != "":
            signature = f.method_decl + "." + f.name.split(".", 1)[1] + ".sig"
            params.insert(0, DeclParam("self" if f.owner == f.method_decl else "self.in", DeclType("class", f.method_decl), missing()))
            if f.owner != f.method_decl:
                if "cell.self" not in locals_:
                    cast = f.owner
                    locals_["self"] = DeclType("class", f.owner)
                elif "self" in locals_: del locals_["self"]
        all_locals: dict[str, DeclType] = {}
        for param in params: all_locals[param.name] = param.ty
        for name, ty in locals_.items(): all_locals[name] = ty
        local_nodes: list[Node] = []
        for name, ty in all_locals.items(): local_nodes.append(Node("Local").put("name", string(name)).put("ty", self.type(ty)))
        result_type = f.sig.ret.args[0] if f.sig.ret.kind == "Coroutine" else DeclType("None")
        request = (Node("MachineRequest").put("version", scalar(1)).put("none", self.type(DeclType("None")))
                   .put("boolean", self.type(DeclType("bool"))).put("exception", self.type(DeclType("class", "BaseException")))
                   .put("result", self.type(result_type)).put("locals", sequence(local_nodes)).put("body", self.block(f.body)))
        graph = MachineGraph(request)
        machine = owned_machine_type(e, f.sig.ret)
        frame_locals: list[Node] = []
        for name, handle in graph.locals.items():
            frame_locals.append(Node("FrameLocal").put("name", string(name)).put("type", owned_wasm_type(e, self.untype(handle))))
        frame_params: list[Node] = []
        for param in params: frame_params.append(Node("FrameLocal").put("name", string(param.name)).put("type", owned_wasm_type(e, param.ty)))
        e.frames.append(Node("FrameType").put("name", string(f.name)).put("machine", machine).put("locals", sequence(frame_locals)))
        e.temps = []
        e.nonnull = []
        e.controls = []
        e.labels = 0
        e.result = result_type
        states: list[Node] = []
        for state in graph.states:
            stmt = state.stmt
            entry = (Node("FrameState").put("kind", string(state.kind)).put("next", scalar(state.next))
                     .put("error", scalar(state.error)).put("other", scalar(state.other)).put("statement", owned_flag(stmt.kind != "none")))
            code = ""
            if state.kind == "branch": code = e.ex(self.unexpression(stmt.get("cond")))
            elif state.kind == "await":
                operand = self.unexpression(stmt.get("operand"))
                entry.put("child_type", owned_machine_type(e, operand.ty)).put("target", string(stmt.get("name").text))
                code = e.ex(operand)
            elif state.kind == "yield":
                if stmt.get("value").kind != "none": code = e.ex(self.unexpression(stmt.get("value")))
            elif state.kind not in ("done", "fail") and stmt.kind != "none": code = e.stmt(self.unstatement(stmt))
            entry.put("code", string(code))
            states.append(entry)
        temps: list[Node] = []
        for temp in e.temps:
            # temp is an owned '(local $e.N TYPE)' declaration, not user text.
            split = temp.find(" ", 7)
            temps.append(Node("Temp").put("name", string(temp[7:split])).put("type", string(temp[split + 1:-1])))
        message = e.literals.lit("generator raised StopIteration" if f.sig.ret.kind == "Iterator" else "coroutine raised StopIteration")
        frame_request = (Node("FrameRequest").put("name", string(f.name)).put("machine", machine).put("locals", sequence(frame_locals))
                         .put("states", sequence(states)).put("result", owned_flag(result_type.kind != "None")).put("stop_message", string(message))
                         .put("self_cast", string(cast)).put("temps", sequence(temps)).put("params", sequence(frame_params))
                         .put("signature", string(signature)).put("entry", scalar(graph.entry)).put("fail", scalar(graph.fail)))
        return machine_frame_wat(frame_request)


def owned_has_await(body: list[BodyStmt]) -> bool:
    for stmt in body:
        for expr in stmt.exprs:
            if owned_expr_await(expr): return True
        if owned_has_await(stmt.body) or owned_has_await(stmt.otherwise) or owned_has_await(stmt.step) or owned_has_await(stmt.finalbody): return True
        for handler in stmt.handlers:
            if owned_has_await(handler.body): return True
    return False


def owned_expr_await(expr: BodyExpr) -> bool:
    if expr.kind == "Await": return True
    if owned_has_await(expr.pre): return True
    for arg in expr.args:
        if owned_expr_await(arg): return True
    return False


def owned_runtime_machine(name: str, params: list[DeclParam], result: DeclType, body: list[BodyStmt]) -> BodyFunction:
    sig = DeclSignature()
    sig.params = params
    sig.ret = DeclType("Coroutine").add(result)
    f = BodyFunction(name, sig)
    f.machine = True
    f.body = body
    return f


def owned_runtime_machines(e: SourceEmitter) -> list[str]:
    out: list[str] = []
    none = DeclType("None")
    n = missing()
    out.append(OwnedMachine(e).run(owned_runtime_machine("snek.checkpoint", [], none, [BodyStmt("Yield", n)])))
    if "stream_drain" in e.io:
        stream = BodyExpr("LocalGet", DeclType("Stream", "BinaryOutput"), n, "stream")
        body = [BodyStmt("ExprStmt", n).add(BodyExpr("Call", none, n, "snek.sys.stream_flush").add(stream))]
        if e.component:
            body.append(BodyStmt("Yield", n))
            body.append(BodyStmt("ExprStmt", n).add(BodyExpr("Call", none, n, "snek.sys.check").add(BodyExpr("Call", DeclType("i32"), n, "snek.sys.fd").add(stream))))
        out.append(OwnedMachine(e).run(owned_runtime_machine("snek.sys.stream_drain", [DeclParam("stream", stream.ty, n)], none, body)))
    if "stream_read" in e.io:
        stream = BodyExpr("LocalGet", DeclType("Stream", "BinaryInput"), n, "stream")
        result = DeclType("list").add(DeclType("i32"))
        params = [DeclParam("stream", stream.ty, n), DeclParam("size", DeclType("i32"), n), DeclParam("line", DeclType("bool"), n)]
        args: list[BodyExpr] = []
        for param in params[1:]: args.append(BodyExpr("LocalGet", param.ty, n, param.name))
        body = [BodyStmt("ExprStmt", n).add(BodyExpr("Call", none, n, "snek.sys.ensure_open").add(stream))]
        call = BodyExpr("Call", none if e.component else result, n, "io.read-start" if e.component else "snek.sys.read-core")
        call.args = args
        if e.component:
            body.extend([BodyStmt("ExprStmt", n).add(call), BodyStmt("Yield", n), BodyStmt("Return", n).add(BodyExpr("Call", result, n, "io.read-result"))])
        else: body.append(BodyStmt("Return", n).add(call))
        out.append(OwnedMachine(e).run(owned_runtime_machine("snek.sys.stream_read", params, result, body)))
    if "sleep" in e.features:
        seconds = BodyExpr("LocalGet", DeclType("f64"), n, "seconds")
        body = [BodyStmt("ExprStmt", n).add(BodyExpr("Call", none, n, "lib.timer-start" if e.component else "lib.sleep-core").add(seconds))]
        if e.component: body.extend([BodyStmt("Yield", n), BodyStmt("ExprStmt", n).add(BodyExpr("Call", none, n, "lib.timer-result"))])
        out.append(OwnedMachine(e).run(owned_runtime_machine("snek.lib.sleep", [DeclParam("seconds", seconds.ty, n)], none, body)))
    return out
