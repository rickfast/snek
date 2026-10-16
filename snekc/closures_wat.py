"""Typed funcrefs paired with GC environments and mutable lexical cells."""
from . import ir
from .types import CallableT, CellT, EnvT, NONE, OptionalT


class ClosureEmitter:
    def closure_types_wat(self):
        lines = ["    (type $env<> (sub (struct)))"]
        for t in self.closure_types.values():
            h = self.heap(t)
            args = "".join(f" (param {self.valtype(p)})" for p in t.params)
            ret = f" (result {self.valtype(t.ret)})" if t.ret != NONE else ""
            lines += [f"    (type {h}.sig (func (param (ref $env<>)){args}{ret}))",
                      f"    (type {h} (struct (field $fn (ref {h}.sig)) (field $env (ref $env<>))))"]
        for t in self.cell_types.values():
            lines.append(f"    (type {self.heap(t)} (struct (field $bound (mut i32)) (field $value (mut {self.storage(t.elem)}))))")
        for t in self.env_types.values():
            fields = "".join(f" (field {self.valtype(p)})" for p in t.captures)
            lines.append(f"    (type {self.heap(t)} (sub $env<> (struct{fields})))")
        return lines

    def closure_artifacts(self):
        lines = []
        if self.closure_refs:
            lines.append("  (elem declare func " + " ".join("$" + n for n in sorted(self.closure_refs)) + ")")
        for name, t in self.function_refs.items():
            h = self.heap(t)
            args = "".join(f" (param $a{i} {self.valtype(p)})" for i, p in enumerate(t.params))
            ret = f" (result {self.valtype(t.ret)})" if t.ret != NONE else ""
            values = "".join(f" (local.get $a{i})" for i in range(len(t.params)))
            lines.append(f"  (func $callable.wrap.{name} (type {h}.sig) (param $env (ref $env<>)){args}{ret} (call ${name}{values}))")
            lines.append(f"  (global $callable.value.{name} (ref {h}) (struct.new {h} (ref.func $callable.wrap.{name}) (struct.new $env<>)))")
        return "\n".join(lines)

    def closure_expr(self, e):
        if isinstance(e, ir.FunctionRef):
            self.heap(e.ty)
            self.function_refs[e.func] = e.ty
            return f"(global.get $callable.value.{e.func})"
        if isinstance(e, ir.Closure):
            self.closure_refs.add(e.func)
            h = self.heap(e.ty)
            env = self.heap(EnvT(tuple(c.ty for c in e.captures)))
            captures = " ".join(self.ex(c) for c in e.captures)
            return f"(struct.new {h} (ref.func ${e.func}) (struct.new {env} {captures}))"
        if isinstance(e, ir.CallValue):
            h = self.heap(e.callee.ty)
            # Evaluate the callee once, before arguments, even when arguments
            # rebind the source cell or mutate the container holding it.
            tmp = self.fn.temp(self.storage(e.callee.ty))
            ret = f" (result {self.valtype(e.ty)})" if e.ty != NONE else ""
            args = " ".join(self.ex(a) for a in e.args)
            return (f"(block{ret} (local.set {tmp} {self.ex(e.callee)}) "
                    f"(call_ref {h}.sig (struct.get {h} $env (local.get {tmp})) {args} "
                    f"(struct.get {h} $fn (local.get {tmp}))))")
        if isinstance(e, ir.CellNew):
            return f"(struct.new_default {self.heap(e.ty)})"
        if isinstance(e, ir.EnvGet):
            h = self.heap(e.layout)
            return f"(struct.get {h} {e.index} (ref.cast (ref {h}) {self.ex(e.env)}))"
        if isinstance(e, ir.CellGet):
            h = self.heap(e.cell.ty)
            tmp = self.fn.temp(self.storage(e.cell.ty))
            msg = (f"cannot access free variable '{e.name}' where it is not associated with a value in enclosing scope"
                   if e.free else f"cannot access local variable '{e.name}' where it is not associated with a value")
            error = self.raise_code("NameError" if e.free else "UnboundLocalError", msg)
            read = f"(struct.get {h} $value (local.get {tmp}))"
            if e.ty.is_ref and not isinstance(e.ty, OptionalT):
                read = f"(ref.cast (ref {self.heap(e.ty)}) {read})"
            return (f"(block (result {self.valtype(e.ty)}) (local.set {tmp} {self.ex(e.cell)}) "
                    f"(if (i32.eqz (struct.get {h} $bound (local.get {tmp}))) (then {error})) {read})")
        raise AssertionError(e)

    def closure_stmt(self, s):
        h = self.heap(s.cell.ty)
        cell = self.ex(s.cell)
        if isinstance(s, ir.CellSet):
            return [f"(struct.set {h} $value {cell} {self.ex(s.value)})",
                    f"(struct.set {h} $bound {cell} (i32.const 1))"]
        assert isinstance(s, ir.CellClear)
        ty = s.cell.ty.elem
        zero = f"(ref.null {self.heap(ty)})" if ty.is_ref else f"({self.valtype(ty)}.const 0)"
        return [f"(struct.set {h} $bound {cell} (i32.const 0))",
                f"(struct.set {h} $value {cell} {zero})"]
