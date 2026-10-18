"""Stackless suspension: normalize awaits, then turn structured IR into a CFG.

Each state records its exception successor. Finally suites are cloned on outgoing
edges, so pending returns and exceptions live in the GC frame across suspension.
No Wasm stack, host stack or program replay is retained between resume calls.
"""
from __future__ import annotations

from dataclasses import dataclass, fields, replace

from . import ir
from .types import BOOL, NONE, ClassInfo, ClassT, CoroutineT, IteratorT, OptionalT, Type


@dataclass
class State:
    stmt: ir.Stmt | None
    next: int
    error: int
    other: int = -1
    kind: str = "stmt"


class Graph:
    def __init__(self, f: ir.Func, exc: ClassInfo):
        self.f = f
        self.exc = exc
        self.states: list[State] = []
        self.locals = dict(f.params) | f.locals
        self.locals["sm.exc"] = ClassT(exc)
        self.result = f.ret.elem if isinstance(f.ret, CoroutineT) else NONE
        if self.result != NONE:
            self.locals["sm.result"] = self.result
        self.end = self.add(None, -1, -1, kind="done")
        self.fail = self.add(None, -1, -1, kind="fail")
        self.entry = self.block(f.body, self.end, self.fail, self.end)

    def add(self, stmt: ir.Stmt | None, next_: int, error: int, other: int = -1, kind: str = "stmt") -> int:
        self.states.append(State(stmt, next_, error, other, kind))
        return len(self.states) - 1

    def temp(self, ty: Type) -> ir.LocalGet:
        name = f"sm.t.{len(self.locals)}"
        self.locals[name] = ty
        return ir.LocalGet(ty, name, ty)

    def expr(self, e: ir.Expr) -> tuple[list[ir.Stmt], ir.Expr]:
        """ANF with explicit branches for lazy expressions; preserve evaluation order."""
        if not has_await(e):
            return [], e
        if isinstance(e, ir.Seq):
            pre, value = self.expr(e.value)
            return [*e.stmts, *pre], value
        if isinstance(e, ir.Await):
            pre, operand = self.expr(e.operand)
            child = self.temp(operand.ty)
            value = self.temp(e.ty) if e.ty != NONE else ir.Call(NONE, "sm.noop", [])
            return [*pre, ir.SetLocal(child.name, operand),
                    ir.ExprStmt(ir.MachineOp(NONE, "claim", child)),
                    ir.AwaitStep(value.name if e.ty != NONE else None, child)], value
        if isinstance(e, (ir.Logical, ir.IfExpr)):
            condition = e.left if isinstance(e, ir.Logical) else e.cond
            pre, condition = self.expr(condition)
            value = self.temp(e.ty)
            if isinstance(e, ir.Logical):
                yes = e.right if e.op == "and" else ir.Const(BOOL, True)
                no = ir.Const(BOOL, False) if e.op == "and" else e.right
            else:
                yes, no = e.then, e.else_
            return [*pre, ir.If(condition, [ir.SetLocal(value.name, yes)],
                               [ir.SetLocal(value.name, no)])], value
        pre = []

        def lower(v):
            if isinstance(v, ir.Expr):
                before, value = self.expr(v)
                pre.extend(before)
                # Capture even local reads: a later operand can mutate their objects
                # or reassign locals through Seq before we resume the enclosing op.
                if value.ty != NONE:
                    tmp = self.temp(value.ty)
                    pre.append(ir.SetLocal(tmp.name, value))
                    return tmp
                pre.append(ir.ExprStmt(value))
                return ir.Call(NONE, "sm.noop", [])
            if isinstance(v, list):
                return [lower(x) for x in v]
            if isinstance(v, tuple):
                return tuple(lower(x) for x in v)
            return v

        return pre, replace(e, **{f.name: lower(getattr(e, f.name)) for f in fields(e) if f.name != "ty"})

    def block(self, body, next_, error, ret, brk=None, cont=None):
        for s in reversed(body):
            next_ = self.stmt(s, next_, error, ret, brk, cont)
        return next_

    def stmt(self, s, next_, error, ret, brk, cont):
        if isinstance(s, ir.If):
            yes = self.block(s.then, next_, error, ret, brk, cont)
            no = self.block(s.else_, next_, error, ret, brk, cont)
            pre, cond = self.expr(s.cond)
            branch = self.add(ir.If(cond, [], []), yes, error, no, "branch")
            return self.block(pre, branch, error, ret, brk, cont)
        if isinstance(s, ir.Loop):
            head = self.add(None, 0, error, kind="goto")
            step = self.block(s.step, head, error, ret, next_, head)
            body = self.block(s.body, step, error, ret, next_, step)
            cond = s.cond or ir.Const(BOOL, True)
            pre, value = self.expr(cond)
            branch = self.add(ir.If(value, [], []), body, error, next_, "branch")
            self.states[head].next = self.block(pre, branch, error, ret, next_, step)
            return head
        if isinstance(s, ir.Try):
            # A finalizer executes with the enclosing region's edges, never its own.
            def finish(target):
                return (self.block(s.finally_ or [], target, error, ret, brk, cont)
                        if target is not None else None)
            after, r, b, c = finish(next_), finish(ret), finish(brk), finish(cont)
            # Save the original exception across a finally that catches another one.
            saved = self.temp(ClassT(self.exc))
            restore = self.add(ir.SetLocal("sm.exc", saved), error, error)
            exceptional = self.add(ir.SetLocal(saved.name, ir.LocalGet(ClassT(self.exc), "sm.exc", ClassT(self.exc))),
                                   finish(restore), error)
            dispatch = exceptional
            caught = ir.LocalGet(ClassT(self.exc), s.exc_local, ClassT(self.exc))
            for h in reversed(s.handlers):
                head = []
                if h.var:
                    # LocalGet's narrowed type emits a checked reference cast.
                    head.append(ir.SetLocal(h.var, ir.LocalGet(h.var_decl, s.exc_local, ClassT(self.exc))))
                target = self.block(head + h.body, after, exceptional, r, b, c)
                if h.classes is None:
                    dispatch = target
                else:
                    for cls in reversed(h.classes):
                        dispatch = self.add(ir.If(ir.IsInstance(BOOL, caught, cls), [], []),
                                            target, exceptional, dispatch, "branch")
            dispatch = self.add(ir.SetLocal(s.exc_local, ir.LocalGet(ClassT(self.exc), "sm.exc", ClassT(self.exc))),
                                dispatch, exceptional)
            else_ = self.block(s.else_, after, exceptional, r, b, c)
            return self.block(s.body, else_, dispatch, r, b, c)
        if isinstance(s, ir.Return):
            return (self.stmt(ir.SetLocal("sm.result", s.value), ret, error, ret, brk, cont)
                    if s.value is not None else ret)
        if isinstance(s, (ir.Break, ir.Continue)):
            target = brk if isinstance(s, ir.Break) else cont
            assert target is not None
            return target
        if isinstance(s, ir.AwaitStep):
            return self.add(s, next_, error, kind="await")
        # Normalize every operand (including Print.args), capturing earlier
        # values before later operands suspend or mutate them.
        pre = []
        def lower(v):
            if isinstance(v, ir.Expr):
                before, value = self.expr(v)
                pre.extend(before)
                if value.ty != NONE:
                    tmp = self.temp(value.ty)
                    pre.append(ir.SetLocal(tmp.name, value))
                    return tmp
                pre.append(ir.ExprStmt(value))
                return ir.Call(NONE, "sm.noop", [])
            if isinstance(v, list):
                return [lower(x) for x in v]
            if isinstance(v, tuple):
                return tuple(lower(x) for x in v)
            return v
        lowered = replace(s, **{f.name: lower(getattr(s, f.name)) for f in fields(s)}) if has_await(s) else s
        kind = "yield" if isinstance(s, ir.Yield) else "stmt"
        target = self.add(lowered, next_, error, kind=kind)
        return self.block(pre, target, error, ret, brk, cont)


def has_await(value: object) -> bool:
    if isinstance(value, (ir.Await, ir.AwaitStep)):
        return True
    if isinstance(value, (ir.Expr, ir.Stmt, ir.Handler)):
        return any(has_await(getattr(value, f.name)) for f in fields(value))
    if isinstance(value, (list, tuple)):
        return any(has_await(v) for v in value)
    return False


class MachineEmitter:
    def machine_fields(self, t):
        B = self.heap(t)
        value = f" (field $value (mut {self.storage(t.elem)}))" if t.elem != NONE else ""
        return ("(field $pc (mut i32)) (field $busy (mut i32)) (field $handler (mut i32)) "
                f"(field $claimed (mut i32)) (field $started (mut i32)) (field $step (ref {B}.sig)){value}")

    def machine_type_section(self):
        if self.machine_backend is not None:
            return self.machine_backend.types(self)
        return self.machine_type_section_python()

    def machine_type_section_python(self):
        out = []
        for t in list(self.machine_types.values()):
            B = self.heap(t)
            out += [f"    (type {B}.sig (func (param (ref {B}) (ref null $BaseException)) (result i32)))",
                    f"    (type {B} (sub (struct {self.machine_fields(t)})))"]
        for f, g in self.machine_graphs:
            B, F = self.heap(f.ret), f"$frame.{f.name}"
            fields_ = " ".join(f"(field $v.{n} (mut {self.storage(t)}))" for n, t in g.locals.items())
            out.append(f"    (type {F} (sub final {B} (struct {self.machine_fields(f.ret)} {fields_})))")
        return out

    def machine_value(self, t, code):
        value = f"(struct.get {self.heap(t)} $value {code})"
        return f"(ref.as_non_null {value})" if t.elem.is_ref and not isinstance(t.elem, OptionalT) else value

    def machine_op(self, e):
        B = self.heap(e.obj.ty)
        if e.op == "value":
            return self.machine_value(e.obj.ty, self.ex(e.obj))
        return self.call(f"{B}.{e.op}", [e.obj, *e.args])

    def emit_machine(self, f):
        g = (self.graph_factory or Graph)(f, next(c for c in self.m.classes if c.name == "BaseException"))
        self.machine_graphs.append((f, g))
        if self.machine_backend is not None:
            return self.machine_backend.frame(self, f, g)
        return self.emit_machine_python(f, g)

    def emit_machine_python(self, f, g):
        from .emit_wat import FnState
        B, F = self.heap(f.ret), f"$frame.{f.name}"
        # Register frame field types before collection/type closure.
        for t in g.locals.values():
            self.storage(t)
        self.fn = FnState(f)
        self.fn.nonnull.clear()  # all frame slots, including parameters, are nullable
        frame = "(local.get $sm.frame)"
        base = "(local.get $sm.base)"
        setpc = lambda n: f"(local.set $sm.pc (i32.const {n}))"
        goto = lambda n: f"{setpc(n)} (br $sm.dispatch)"
        save = " ".join(f"(struct.set {F} $v.{n} {frame} (local.get ${n}))" for n in g.locals)
        clear = " ".join(f"(struct.set {F} $v.{n} {frame} (ref.null {self.heap(t)}))"
                         for n, t in g.locals.items() if t.is_ref)
        if isinstance(f.ret, IteratorT) and f.ret.elem.is_ref:
            clear += f" (struct.set {B} $value {base} (ref.null {self.heap(f.ret.elem)}))"
        finish = f"(struct.set {B} $pc {base} (i32.const -1)) (struct.set {B} $busy {base} (i32.const 0)) {clear}"
        cases = []
        for i, state in enumerate(g.states):
            code = f"(local.set $sm.handler (i32.const {state.error})) "
            s = state.stmt
            if state.kind == "done":
                if g.result != NONE:
                    code += f"(struct.set {B} $value {base} (local.get $sm.result)) "
                code += f"{finish} (return (i32.const 0))"
            elif state.kind == "fail":
                code += finish
                message = "generator raised StopIteration" if isinstance(f.ret, IteratorT) else "coroutine raised StopIteration"
                code += f" (if (i32.and (i32.eqz (local.get $sm.unstarted)) (ref.test (ref $StopIteration) (local.get $sm.exc))) (then (throw $snek.exc (call $RuntimeError.__new__ {self.lit(message)}))))"
                code += " (throw $snek.exc (ref.as_non_null (local.get $sm.exc)))"
            elif state.kind == "branch":
                code += f"(if {self.ex(s.cond)} (then {setpc(state.next)}) (else {setpc(state.other)})) (br $sm.dispatch)"
            elif state.kind in ("yield", "await"):
                if state.kind == "await":
                    C = self.heap(s.operand.ty)
                    child = self.ex(s.operand)
                    code += f"(if (i32.eqz (call {C}.resume {child} (local.get $sm.inject))) (then "
                    code += "(local.set $sm.inject (ref.null $BaseException)) "
                    if s.name:
                        code += f"(local.set ${s.name} {self.machine_value(s.operand.ty, child)}) "
                    code += goto(state.next) + ")) "
                    resume = i
                else:
                    resume = state.next
                    if isinstance(f.ret, CoroutineT):
                        code += "(global.set $sm.yielded (i32.const 1)) "
                    if s.value is not None:
                        code += f"(struct.set {B} $value {base} {self.ex(s.value)}) "
                code += f"{save} (struct.set {B} $pc {base} (i32.const {resume})) (struct.set {B} $handler {base} (i32.const {state.error})) (struct.set {B} $busy {base} (i32.const 0)) (return (i32.const 1))"
            else:
                if s is not None:
                    code += " ".join(self.stmt(s)) + " "
                code += goto(state.next)
            cases.append(f"(if (i32.eq (local.get $sm.pc) (i32.const {i})) (then {code}))")
        await_states = [i for i, s in enumerate(g.states) if s.kind == "await"]
        isawait = "(i32.const 0)"
        for i in await_states:
            isawait = f"(i32.or {isawait} (i32.eq (local.get $sm.pc) (i32.const {i})))"
        loads = " ".join(f"(local.set ${n} (struct.get {F} $v.{n} {frame}))" for n in g.locals)
        if f.self_cast:
            loads += f" (local.set $self (ref.cast (ref ${f.self_cast.name}) (local.get $self.in)))"
        locals_ = " ".join(f"(local ${n} {self.storage(t)})" for n, t in g.locals.items())
        locals_ += " " + " ".join(f"(local {n} {t})" for n, t in self.fn.temps)
        # Fail is outside the catch region, otherwise escaping exceptions loop forever.
        fail_code = cases[g.fail]
        cases[g.fail] = ""
        step = f"""(func ${f.name}.step (type {B}.sig)
          (param $sm.base (ref {B})) (param $sm.inject (ref null $BaseException)) (result i32)
          (local $sm.frame (ref {F})) (local $sm.pc i32) (local $sm.handler i32) (local $sm.unstarted i32) {locals_}
          (local.set $sm.frame (ref.cast (ref {F}) {base}))
          (local.set $sm.unstarted (i32.and (i32.eqz (struct.get {B} $started {base})) (i32.eqz (ref.is_null (local.get $sm.inject)))))
          (struct.set {B} $started {base} (i32.const 1))
          {loads}
          (local.set $sm.pc (struct.get {B} $pc {base}))
          (if (i32.and (i32.eqz (ref.is_null (local.get $sm.inject))) (i32.eqz {isawait}))
            (then (local.set $sm.exc (local.get $sm.inject))
              (local.set $sm.inject (ref.null $BaseException))
              (local.set $sm.pc (struct.get {B} $handler {base}))))
          (loop $sm.dispatch
            {fail_code}
            (local.set $sm.exc
              (block $sm.caught (result (ref $BaseException))
                (try_table (catch $snek.exc $sm.caught)
                  {' '.join(cases)} (unreachable)) (unreachable)))
            (local.set $sm.inject (ref.null $BaseException))
            (local.set $sm.pc (local.get $sm.handler)) (br $sm.dispatch)) (unreachable))"""
        default = lambda t: f"(ref.null {self.heap(t)})" if t.is_ref else f"({self.valtype(t)}.const 0)"
        params = dict(f.params)
        vals = " ".join(f"(local.get ${n})" if n in params else default(t) for n, t in g.locals.items())
        value = " " + default(f.ret.elem) if f.ret.elem != NONE else ""
        sig = f" (type ${f.sig_type})" if f.sig_type else ""
        factory = (f"(func ${f.name}{sig} " + " ".join(f"(param ${n} {self.valtype(t)})" for n, t in f.params)
                   + f" (result (ref {B})) (struct.new {F} (i32.const {g.entry}) (i32.const 0) (i32.const {g.fail}) (i32.const 0) (i32.const 0) (ref.func ${f.name}.step){value} {vals}))")
        self.fn = None
        return f"(elem declare func ${f.name}.step)\n{factory}\n{step}"

    def machine_helpers(self):
        if self.machine_backend is not None:
            return self.machine_backend.helpers(self)
        return self.machine_helpers_python()

    def machine_helpers_python(self):
        out = ["(func $sm.noop)"]
        for t in list(self.machine_types.values()):
            B = self.heap(t)
            a = "(local.get $a)"
            null = "(ref.null $BaseException)"
            error = lambda cls, msg: f"(throw $snek.exc (call ${cls}.__new__ {self.lit(msg)}))"
            out.append(f"""(func {B}.resume (param $a (ref {B})) (param $e (ref null $BaseException)) (result i32)
              (if (struct.get {B} $busy {a}) (then {error('ValueError' if isinstance(t, IteratorT) else 'RuntimeError', 'generator already executing' if isinstance(t, IteratorT) else 'coroutine already executing')}))
              (if (i32.eq (struct.get {B} $pc {a}) (i32.const -1))
                (then (if (i32.eqz (ref.is_null (local.get $e))) (then (throw $snek.exc (ref.as_non_null (local.get $e))))) (return (i32.const 0))))
              (struct.set {B} $busy {a} (i32.const 1))
              (call_ref {B}.sig {a} (local.get $e) (struct.get {B} $step {a})))
              (func {B}.advance (param $a (ref {B})) (result i32) (call {B}.resume {a} {null}))""")
            if isinstance(t, CoroutineT):
                out.append(f"""(func {B}.claim (param $a (ref {B}))
                  (if (struct.get {B} $claimed {a}) (then {error('RuntimeError', 'cannot reuse already awaited coroutine')}))
                  (struct.set {B} $claimed {a} (i32.const 1)))""")
                continue
            value = self.machine_value(t, a)
            R = self.valtype(t.elem)
            out += [f"""(func {B}.next (param $a (ref {B})) (result {R})
                (if (i32.eqz (call {B}.advance {a})) (then (throw $snek.exc (call $StopIteration.__new__ (ref.null $str))))) {value})""",
                f"""(func {B}.next_default (param $a (ref {B})) (param $d {R}) (result {R})
                (if (result {R}) (call {B}.advance {a}) (then {value}) (else (local.get $d))))""",
                f"""(func {B}.throw (param $a (ref {B})) (param $e (ref $BaseException)) (result {R})
                (if (i32.eqz (call {B}.resume {a} (local.get $e))) (then (throw $snek.exc (call $StopIteration.__new__ (ref.null $str))))) {value})""",
                f"""(func {B}.close (param $a (ref {B})) (local $e (ref $BaseException))
                (local.set $e (block $caught (result (ref $BaseException))
                  (try_table (catch $snek.exc $caught)
                    (if (call {B}.resume {a} (call $GeneratorExit.__new__ (ref.null $str)))
                      (then {error('RuntimeError', 'generator ignored GeneratorExit')})) (return)) (unreachable)))
                (if (i32.eqz (i32.or (ref.test (ref $GeneratorExit) (local.get $e)) (ref.test (ref $StopIteration) (local.get $e)))) (then (throw $snek.exc (local.get $e)))))"""]
        return "\n".join(out)
