"""Snek-owned suspension lowering over a lossless, versioned IR wire tree.

Types and declaration metadata are opaque handles: this pass only rearranges
already checked expressions. No Python parser, reflection or CFG builder runs
in the compiled lowerer.
"""
from __future__ import annotations
from snek import i32
from bootstrap.parser import Node, missing, scalar, sequence, string


class MachineJSON:
    text: str
    pos: i32

    def __init__(self, text: str) -> None:
        self.text = text
        self.pos = 0

    def space(self) -> None:
        while self.pos < len(self.text) and self.text[self.pos] in " \r\n\t":
            self.pos += 1

    def take(self, expected: str) -> None:
        self.space()
        if not self.text.startswith(expected, self.pos):
            raise ValueError("invalid machine JSON")
        self.pos += len(expected)

    def quoted(self) -> str:
        self.take('"')
        out: list[str] = []
        while self.pos < len(self.text):
            char = self.text[self.pos]
            self.pos += 1
            if char == '"':
                return "".join(out)
            if char == "\\":
                if self.pos >= len(self.text):
                    raise ValueError("unfinished machine JSON escape")
                char = self.text[self.pos]
                self.pos += 1
                if char == "u":
                    code = 0
                    for unused in range(4):
                        if self.pos >= len(self.text):
                            raise ValueError("unfinished machine JSON Unicode escape")
                        digit = "0123456789abcdef".find(self.text[self.pos].lower())
                        if digit < 0:
                            raise ValueError("invalid machine JSON Unicode escape")
                        code = code * 16 + digit
                        self.pos += 1
                    if code >= 55296 and code <= 57343:
                        raise ValueError("machine JSON requires UTF-8, not surrogate escapes")
                    char = chr(code)
                elif char in '"\\/':
                    pass
                elif char == "n": char = "\n"
                elif char == "r": char = "\r"
                elif char == "t": char = "\t"
                elif char == "b": char = "\b"
                elif char == "f": char = "\f"
                else: raise ValueError("invalid machine JSON escape")
            elif ord(char) < 32:
                raise ValueError("control character in machine JSON")
            out.append(char)
        raise ValueError("unfinished machine JSON string")

    def value(self) -> Node:
        self.space()
        if self.pos >= len(self.text):
            raise ValueError("unfinished machine JSON")
        char = self.text[self.pos]
        if char == '"': return string(self.quoted())
        if char == "[":
            self.pos += 1
            items: list[Node] = []
            self.space()
            if not self.text.startswith("]", self.pos):
                items.append(self.value())
                self.space()
                while self.text.startswith(",", self.pos):
                    self.pos += 1
                    items.append(self.value())
                    self.space()
            self.take("]")
            return sequence(items)
        if char == "{":
            self.pos += 1
            result = Node("")
            self.space()
            more = not self.text.startswith("}", self.pos)
            seen: list[str] = []
            while more:
                key = self.quoted()
                if key in seen: raise ValueError("duplicate machine JSON field")
                seen.append(key)
                self.take(":")
                value = self.value()
                if key == "_type":
                    if value.kind != "str": raise ValueError("invalid machine JSON tag")
                    result.kind = value.text
                else: result.put(key, value)
                self.space()
                more = self.text.startswith(",", self.pos)
                if more: self.pos += 1
            self.take("}")
            if result.kind == "": raise ValueError("missing machine JSON tag")
            return result
        if char == "n":
            self.take("null")
            return missing()
        if char == "t":
            self.take("true")
            return Node("scalar", "true")
        if char == "f":
            self.take("false")
            return Node("scalar", "false")
        # The transport represents numeric constants as opaque handles. Only
        # signed i32 handles/edges/version numbers are numeric JSON tokens.
        start = self.pos
        if char == "-": self.pos += 1
        digits = self.pos
        while self.pos < len(self.text) and self.text[self.pos] in "0123456789":
            self.pos += 1
        if self.pos == digits: raise ValueError("invalid machine JSON number")
        if self.pos - digits > 1 and self.text[digits] == "0":
            raise ValueError("invalid machine JSON leading zero")
        return scalar(i32(self.text[start:self.pos]))

    def read(self) -> Node:
        result = self.value()
        self.space()
        if self.pos != len(self.text): raise ValueError("trailing machine JSON input")
        return result


def machine_has_await(node: Node) -> bool:
    if node.kind == "Await" or node.kind == "AwaitStep": return True
    for item in node.items:
        if machine_has_await(item): return True
    return False


def machine_local(name: str, ty: Node, declared: Node) -> Node:
    return Node("LocalGet").put("ty", ty).put("name", string(name)).put("declared", declared)


def machine_set(name: str, value: Node) -> Node:
    return Node("SetLocal").put("name", string(name)).put("value", value)


def machine_if(cond: Node, yes: list[Node], no: list[Node]) -> Node:
    return Node("If").put("cond", cond).put("then", sequence(yes)).put("else_", sequence(no))


class MachineState:
    stmt: Node
    next: i32
    error: i32
    other: i32
    kind: str

    def __init__(self, stmt: Node, next_: i32, error: i32, other: i32, kind: str) -> None:
        self.stmt = stmt
        self.next = next_
        self.error = error
        self.other = other
        self.kind = kind

    def tree(self) -> Node:
        return Node("State").put("stmt", self.stmt).put("next", scalar(self.next)).put("error", scalar(self.error)).put("other", scalar(self.other)).put("kind", string(self.kind))


class MachineGraph:
    states: list[MachineState]
    locals: dict[str, Node]
    none: Node
    boolean: Node
    exc: Node
    result: Node
    end: i32
    fail: i32
    entry: i32

    def __init__(self, request: Node) -> None:
        if request.kind != "MachineRequest" or request.get("version").text != "1":
            raise ValueError("unsupported machine wire version")
        self.states = []
        self.locals = {}
        self.none = request.get("none")
        self.boolean = request.get("boolean")
        self.exc = request.get("exception")
        self.result = request.get("result")
        self.entry = -1
        self.end = -1
        self.fail = -1
        for item in request.get("locals").items:
            self.locals[item.get("name").text] = item.get("ty")
        self.locals["sm.exc"] = self.exc
        if not self.is_none(self.result): self.locals["sm.result"] = self.result
        self.end = self.add(missing(), -1, -1, -1, "done")
        self.fail = self.add(missing(), -1, -1, -1, "fail")
        self.entry = self.block(request.get("body").items, self.end, self.fail, self.end, -1, -1)

    def is_none(self, ty: Node) -> bool:
        return ty.get("id").text == self.none.get("id").text

    def add(self, stmt: Node, next_: i32, error: i32, other: i32 = -1, kind: str = "stmt") -> i32:
        self.states.append(MachineState(stmt, next_, error, other, kind))
        return len(self.states) - 1

    def temp(self, ty: Node) -> Node:
        name = "sm.t." + str(len(self.locals))
        self.locals[name] = ty
        return machine_local(name, ty, ty)

    def noop(self) -> Node:
        return Node("Call").put("ty", self.none).put("func", string("sm.noop")).put("args", sequence([]))

    def truth(self, value: bool) -> Node:
        return Node("Const").put("ty", self.boolean).put("value", Node("scalar", "true" if value else "false"))

    def lower(self, node: Node, pre: list[Node]) -> Node:
        if "ty" in node.keys:
            pair = self.expr(node)
            pre.extend(pair[0])
            value = pair[1]
            if not self.is_none(value.get("ty")):
                tmp = self.temp(value.get("ty"))
                pre.append(machine_set(tmp.get("name").text, value))
                return tmp
            pre.append(Node("ExprStmt").put("expr", value))
            return self.noop()
        if node.kind == "list" or node.kind == "Tuple":
            result = Node(node.kind, node.text)
            result.keys = node.keys
            for item in node.items: result.items.append(self.lower(item, pre))
            return result
        return node

    def expr(self, expr: Node) -> tuple[list[Node], Node]:
        pre: list[Node] = []
        if not machine_has_await(expr): return (pre, expr)
        if expr.kind == "Seq":
            pair = self.expr(expr.get("value"))
            return (expr.get("stmts").items + pair[0], pair[1])
        if expr.kind == "Await":
            pair = self.expr(expr.get("operand"))
            pre.extend(pair[0])
            child = self.temp(pair[1].get("ty"))
            value = self.noop() if self.is_none(expr.get("ty")) else self.temp(expr.get("ty"))
            pre.append(machine_set(child.get("name").text, pair[1]))
            claim = Node("MachineOp").put("ty", self.none).put("op", string("claim")).put("obj", child).put("args", sequence([]))
            pre.append(Node("ExprStmt").put("expr", claim))
            name = missing() if self.is_none(expr.get("ty")) else value.get("name")
            pre.append(Node("AwaitStep").put("name", name).put("operand", child))
            return (pre, value)
        if expr.kind == "Logical" or expr.kind == "IfExpr":
            cond = expr.get("left") if expr.kind == "Logical" else expr.get("cond")
            pair = self.expr(cond)
            value = self.temp(expr.get("ty"))
            if expr.kind == "Logical":
                yes = expr.get("right") if expr.get("op").text == "and" else self.truth(True)
                no = self.truth(False) if expr.get("op").text == "and" else expr.get("right")
            else:
                yes = expr.get("then")
                no = expr.get("else_")
            pair[0].append(machine_if(pair[1], [machine_set(value.get("name").text, yes)], [machine_set(value.get("name").text, no)]))
            return (pair[0], value)
        result = Node(expr.kind)
        for index in range(len(expr.keys)):
            key = expr.keys[index]
            result.put(key, expr.items[index] if key == "ty" else self.lower(expr.items[index], pre))
        return (pre, result)

    def block(self, body: list[Node], next_: i32, error: i32, ret: i32, brk: i32, cont: i32) -> i32:
        for index in range(len(body) - 1, -1, -1):
            next_ = self.stmt(body[index], next_, error, ret, brk, cont)
        return next_

    def finish(self, body: list[Node], target: i32, error: i32, ret: i32, brk: i32, cont: i32) -> i32:
        if target < 0: return target
        return self.block(body, target, error, ret, brk, cont)

    def stmt(self, stmt: Node, next_: i32, error: i32, ret: i32, brk: i32, cont: i32) -> i32:
        if stmt.kind == "If":
            yes = self.block(stmt.get("then").items, next_, error, ret, brk, cont)
            no = self.block(stmt.get("else_").items, next_, error, ret, brk, cont)
            pair = self.expr(stmt.get("cond"))
            branch = self.add(machine_if(pair[1], [], []), yes, error, no, "branch")
            return self.block(pair[0], branch, error, ret, brk, cont)
        if stmt.kind == "Loop":
            head = self.add(missing(), 0, error, -1, "goto")
            step = self.block(stmt.get("step").items, head, error, ret, next_, head)
            body = self.block(stmt.get("body").items, step, error, ret, next_, step)
            cond = stmt.get("cond")
            if cond.kind == "none": cond = self.truth(True)
            pair = self.expr(cond)
            branch = self.add(machine_if(pair[1], [], []), body, error, next_, "branch")
            self.states[head].next = self.block(pair[0], branch, error, ret, next_, step)
            return head
        if stmt.kind == "Try":
            final = stmt.get("finally_").items
            after = self.finish(final, next_, error, ret, brk, cont)
            r = self.finish(final, ret, error, ret, brk, cont)
            b = self.finish(final, brk, error, ret, brk, cont)
            c = self.finish(final, cont, error, ret, brk, cont)
            saved = self.temp(self.exc)
            restore = self.add(machine_set("sm.exc", saved), error, error)
            exceptional = self.add(machine_set(saved.get("name").text, machine_local("sm.exc", self.exc, self.exc)), self.finish(final, restore, error, ret, brk, cont), error)
            dispatch = exceptional
            caught = machine_local(stmt.get("exc_local").text, self.exc, self.exc)
            handlers = stmt.get("handlers").items
            for index in range(len(handlers) - 1, -1, -1):
                handler = handlers[index]
                prefix: list[Node] = []
                if handler.get("var").kind != "none":
                    prefix.append(machine_set(handler.get("var").text, machine_local(stmt.get("exc_local").text, handler.get("var_decl"), self.exc)))
                target = self.block(prefix + handler.get("body").items, after, exceptional, r, b, c)
                classes = handler.get("classes")
                if classes.kind == "none": dispatch = target
                else:
                    for ci in range(len(classes.items) - 1, -1, -1):
                        test = Node("IsInstance").put("ty", self.boolean).put("operand", caught).put("cls", classes.items[ci])
                        dispatch = self.add(machine_if(test, [], []), target, exceptional, dispatch, "branch")
            dispatch = self.add(machine_set(stmt.get("exc_local").text, machine_local("sm.exc", self.exc, self.exc)), dispatch, exceptional)
            else_ = self.block(stmt.get("else_").items, after, exceptional, r, b, c)
            return self.block(stmt.get("body").items, else_, dispatch, r, b, c)
        if stmt.kind == "Return":
            if stmt.get("value").kind == "none": return ret
            return self.stmt(machine_set("sm.result", stmt.get("value")), ret, error, ret, brk, cont)
        if stmt.kind == "Break" or stmt.kind == "Continue":
            target = brk if stmt.kind == "Break" else cont
            if target < 0: raise ValueError("machine loop edge outside loop")
            return target
        if stmt.kind == "AwaitStep": return self.add(stmt, next_, error, -1, "await")
        pre: list[Node] = []
        lowered = stmt
        if machine_has_await(stmt):
            lowered = Node(stmt.kind)
            for index in range(len(stmt.keys)):
                lowered.put(stmt.keys[index], self.lower(stmt.items[index], pre))
        target = self.add(lowered, next_, error, -1, "yield" if stmt.kind == "Yield" else "stmt")
        return self.block(pre, target, error, ret, brk, cont)

    def tree(self) -> Node:
        locals_: list[Node] = []
        for name in self.locals:
            locals_.append(Node("Local").put("name", string(name)).put("ty", self.locals[name]))
        states: list[Node] = []
        for state in self.states: states.append(state.tree())
        return Node("MachineGraph").put("version", scalar(1)).put("locals", sequence(locals_)).put("result", self.result).put("states", sequence(states)).put("entry", scalar(self.entry)).put("end", scalar(self.end)).put("fail", scalar(self.fail))
