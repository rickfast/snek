"""Direct owned typed-IR emission. This module is Snek source, not a host adapter."""
from __future__ import annotations
from snek import i32
from bootstrap.parser import Node, missing
from bootstrap.declarations import DeclType, DeclParam, Declarations
from bootstrap.bodies import BodyExpr, BodyStmt, BodyFunction, BodyChecker, inner_type
from bootstrap.emit_support import EmitTypes, EmitLiterals, EmitUnicode, emit_mangle, emit_utf8


def emit_float(text: str) -> str:
    values: list[i32] = []
    for word in text[4:].split(","): values.append(i32(word))
    if not text.startswith("f64:") or len(values) != 8:
        raise ValueError("invalid emitter floating constant")
    sign = "-" if values[7] >= 128 else ""
    exponent = ((values[7] & 127) << 4) | (values[6] >> 4)
    digits = "0123456789abcdef"
    fraction = digits[values[6] & 15]
    for i in range(5, -1, -1):
        fraction += digits[values[i] >> 4] + digits[values[i] & 15]
    if exponent == 2047:
        return sign + "inf" if fraction == "0000000000000" else "nan"
    return sign + ("0x0." if exponent == 0 else "0x1.") + fraction + "p" + str(-1022 if exponent == 0 else exponent - 1023)


class EmitControl:
    kind: str
    exit: str
    cont: str
    pending: str
    ret: str
    used: list[str]

    def __init__(self, kind: str, exit: str, cont: str = "", pending: str = "") -> None:
        self.kind = kind
        self.exit = exit
        self.cont = cont
        self.pending = pending
        self.ret = ""
        self.used = []


class SourceEmitter:
    checked: BodyChecker
    types: EmitTypes
    literals: EmitLiterals
    temps: list[str]
    nonnull: list[str]
    controls: list[EmitControl]
    labels: i32
    result: DeclType
    ops: dict[str, list[str]]
    optional_reprs: dict[str, DeclType]
    function_refs: dict[str, DeclType]
    closure_refs: list[str]
    keyed: dict[str, BodyExpr]
    equalities: dict[str, list[DeclType]]
    class_strings: list[str]
    builders: list[str]
    features: list[str]
    io: list[str]
    byte_io: bool
    component: bool
    frames: list[Node]

    def __init__(self, checked: BodyChecker, component: bool = False) -> None:
        self.checked = checked
        self.types = EmitTypes()
        self.literals = EmitLiterals()
        self.temps = []
        self.nonnull = []
        self.controls = []
        self.labels = 0
        self.result = DeclType("None")
        self.ops = {}
        self.optional_reprs = {}
        self.function_refs = {}
        self.closure_refs = []
        self.keyed = {}
        self.equalities = {}
        self.class_strings = []
        self.builders = []
        self.features = []
        self.io = []
        self.byte_io = False
        self.component = component
        self.frames = []

    def temp(self, ty: str) -> str:
        name = "$e." + str(len(self.temps))
        self.temps.append("(local " + name + " " + ty + ")")
        return name

    def label(self) -> str:
        self.labels += 1
        return str(self.labels)

    def local(self, ty: DeclType, name: str) -> str:
        code = "(local.get " + name + ")"
        return "(ref.as_non_null " + code + ")" if ty.reference() and ty.kind != "optional" else code

    def zero(self, ty: DeclType) -> str:
        return "(ref.null " + self.types.heap(ty) + ")" if ty.reference() else "(" + self.types.valtype(ty) + ".const 0)"

    def error(self, cls: str, message: str) -> str:
        return "(throw $snek.exc (call $" + cls + ".__new__ " + self.literals.lit(message) + "))"

    def operation(self, ty: DeclType, op: str) -> str:
        ty = inner_type(ty)
        name = self.types.heap(ty)
        if ty.kind == "list" and op == "to_str": return name + ".to_str"
        if name not in self.ops: self.ops[name] = []
        if ty.kind == "dict":
            if "core" not in self.ops[name]: self.ops[name].append("core")
            if op in ("new", "find", "grow", "set"): return name + "." + op
        if op not in self.ops[name]: self.ops[name].append(op)
        return name + "." + op

    def object_eq(self, left: DeclType, right: DeclType, a: str, b: str, identity: bool = False) -> str:
        name = "$protocol.eq." + emit_mangle(left) + "." + emit_mangle(right)
        self.equalities[name] = [left, right]
        return f"(call {name} {a} {b} (i32.const {1 if identity else 0}))"

    def equal(self, ty: DeclType, a: str, b: str) -> str:
        if ty.kind in ("bool", "i32", "i64", "f32", "f64"):
            return "(" + self.types.valtype(ty) + ".eq " + a + " " + b + ")"
        if ty.kind == "str": return f"(call $rt.str_eq {a} {b})"
        if ty.kind == "optional" and ty.args[0].kind == "str": return f"(call $rt.opt_str_eq {a} {b})"
        inner = inner_type(ty)
        if inner.kind == "class": return self.object_eq(ty, ty, a, b, True)
        if inner.kind in ("list", "tuple", "dict"):
            return f"(call {self.operation(inner, 'eq')} {a} {b})"
        return f"(ref.eq {a} {b})"

    def to_string(self, ty: DeclType, code: str, representation: bool = False) -> str:
        if ty.kind == "str": return f"(call $rt.str_repr {code})" if representation else code
        if ty.kind == "optional" and ty.args[0].kind == "str":
            if representation: return f"(call $rt.opt_str_repr {code})"
            tmp = self.temp("(ref null $str)")
            return f"(if (result (ref $str)) (ref.is_null (local.tee {tmp} {code})) (then {self.literals.lit('None')}) (else (ref.as_non_null (local.get {tmp}))))"
        if ty.kind == "optional":
            name = self.types.heap(ty.args[0])[1:]
            self.optional_reprs[name] = ty.args[0]
            return f"(call $optional.{name}.repr {code})"
        if ty.kind == "i32": return f"(call $rt.i64_to_str (i64.extend_i32_s {code}))"
        if ty.kind == "i64": return f"(call $rt.i64_to_str {code})"
        if ty.kind == "f32": return f"(call $rt.f64_to_str (f64.promote_f32 {code}))"
        if ty.kind == "f64": return f"(call $rt.f64_to_str {code})"
        if ty.kind == "bool": return f"(call $rt.bool_to_str {code})"
        if ty.kind in ("list", "tuple", "dict", "view"):
            return f"(call {self.operation(ty, 'to_str')} {code})"
        if ty.kind == "class":
            if self.checked.decl.subclass(ty.name, "BaseException"): return f"(call $rt.exc_str {code})"
            if ty.name not in self.class_strings: self.class_strings.append(ty.name)
            return f"(call $protocol.str.{ty.name} {code})"
        raise ValueError("no string emission for " + ty.display())

    def call(self, target: str, args: list[BodyExpr]) -> str:
        if target == "$sm.noop": return "(nop)"
        values = ""
        for arg in args: values += " " + self.ex(arg)
        return "(call " + target + values + ")"

    def ex(self, e: BodyExpr) -> str:
        kind = e.kind
        ty = e.ty
        text = e.text
        args = e.args
        if kind == "Const":
            if ty.kind == "bool": return "(i32.const " + ("1" if text == "true" else "0") + ")"
            return "(" + ty.kind + ".const " + (emit_float(text) if ty.kind in ("f32", "f64") else text) + ")"
        if kind == "StrLit": return self.literals.lit(text)
        if kind == "NoneLit": return "(ref.null " + self.types.heap(ty) + ")"
        if kind == "LocalGet":
            code = "(local.get $" + text + ")"
            if ty.reference() and ty.kind != "optional":
                if not ty.same(inner_type(e.declared)): return "(ref.cast (ref " + self.types.heap(ty) + ") " + code + ")"
                if text not in self.nonnull: return "(ref.as_non_null " + code + ")"
            return code
        if kind == "FunctionRef":
            self.types.heap(ty)
            self.function_refs[text] = ty
            return "(global.get $callable.value." + text + ")"
        if kind == "Closure":
            if text not in self.closure_refs: self.closure_refs.append(text)
            heap = self.types.heap(ty)
            env = DeclType("Env", "layout")
            for arg in args: env.add(arg.ty)
            env_heap = self.types.heap(env)
            captures: list[str] = []
            for arg in args: captures.append(self.ex(arg))
            return f"(struct.new {heap} (ref.func ${text}) (struct.new {env_heap} {' '.join(captures)}))"
        if kind == "CallValue":
            heap = self.types.heap(args[0].ty)
            tmp = self.temp(self.types.storage(args[0].ty))
            ret = " (result " + self.types.valtype(ty) + ")" if ty.kind != "None" else ""
            call_values: list[str] = []
            for arg in args[1:]: call_values.append(self.ex(arg))
            return f"(block{ret} (local.set {tmp} {self.ex(args[0])}) (call_ref {heap}.sig (struct.get {heap} $env (local.get {tmp})) {' '.join(call_values)} (struct.get {heap} $fn (local.get {tmp}))))"
        if kind == "CellNew": return "(struct.new_default " + self.types.heap(ty) + ")"
        if kind == "EnvGet":
            heap = self.types.heap(e.declared)
            return f"(struct.get {heap} {text} (ref.cast (ref {heap}) {self.ex(args[0])}))"
        if kind in ("CellGet", "FreeCellGet"):
            heap = self.types.heap(args[0].ty)
            tmp = self.temp(self.types.storage(args[0].ty))
            message = "cannot access " + ("free" if kind == "FreeCellGet" else "local") + " variable '" + text + "' where it is not associated with a value"
            if kind == "FreeCellGet": message += " in enclosing scope"
            error = self.error("NameError" if kind == "FreeCellGet" else "UnboundLocalError", message)
            read = f"(struct.get {heap} $value (local.get {tmp}))"
            if ty.reference() and ty.kind != "optional": read = f"(ref.cast (ref {self.types.heap(ty)}) {read})"
            return f"(block (result {self.types.valtype(ty)}) (local.set {tmp} {self.ex(args[0])}) (if (i32.eqz (struct.get {heap} $bound (local.get {tmp}))) (then {error})) {read})"
        if kind == "Unary":
            value = self.ex(args[0])
            if text == "not": return f"(i32.eqz {value})"
            if text == "neg": return f"({ty.kind}.neg {value})" if ty.kind in ("f32", "f64") else f"({ty.kind}.sub ({ty.kind}.const 0) {value})"
            if text == "invert": return f"({ty.kind}.xor {value} ({ty.kind}.const -1))"
            if text == "abs":
                if ty.kind in ("f32", "f64"): return f"({ty.kind}.abs {value})"
                tmp = self.temp(ty.kind)
                return f"(select ({ty.kind}.sub ({ty.kind}.const 0) (local.tee {tmp} {value})) (local.get {tmp}) ({ty.kind}.lt_s (local.get {tmp}) ({ty.kind}.const 0)))"
        if kind == "Binary":
            left = self.ex(args[0])
            right = self.ex(args[1])
            primitive = ty.kind
            if text in ("min", "max"):
                a = self.temp(primitive)
                b = self.temp(primitive)
                cmp = ("lt" if text == "min" else "gt") + ("_s" if primitive in ("i32", "i64") else "")
                return f"(block (result {primitive}) (local.set {a} {left}) (local.set {b} {right}) (select (local.get {b}) (local.get {a}) ({primitive}.{cmp} (local.get {b}) (local.get {a}))))"
            simple = {"add": "add", "sub": "sub", "mul": "mul", "bitand": "and", "bitor": "or", "bitxor": "xor", "shl": "shl", "shr": "shr_s"}
            if text in simple: return f"({primitive}.{simple[text]} {left} {right})"
            if primitive in ("i32", "i64"): return f"(call $rt.{primitive}.{text} {left} {right})"
            if text == "div_int": return f"(call $rt.f64.div_int {left} {right})"
            if primitive == "f64": return f"(call $rt.f64.{text} {left} {right})"
            return f"(f32.demote_f64 (call $rt.f64.{text} (f64.promote_f32 {left}) (f64.promote_f32 {right})))"
        if kind == "Compare":
            left = self.ex(args[0])
            right = self.ex(args[1])
            operand = args[0].ty
            if text in ("is", "isnot"):
                code = f"(ref.eq {left} {right})"
                return f"(i32.eqz {code})" if text == "isnot" else code
            if operand.kind == "str":
                if text in ("eq", "ne"):
                    code = f"(call $rt.str_eq {left} {right})"
                    return f"(i32.eqz {code})" if text == "ne" else code
                return f"(i32.{text}_s (call $rt.str_cmp {left} {right}) (i32.const 0))"
            op = text + ("_s" if operand.kind in ("i32", "i64") and text not in ("eq", "ne") else "")
            return f"({self.types.valtype(operand)}.{op} {left} {right})"
        if kind == "ObjectEq": return self.object_eq(args[0].ty, args[1].ty, self.ex(args[0]), self.ex(args[1]))
        if kind == "Logical":
            left = self.ex(args[0])
            right = self.ex(args[1])
            return f"(if (result i32) {left} (then {right}) (else (i32.const 0)))" if text == "and" else f"(if (result i32) {left} (then (i32.const 1)) (else {right}))"
        if kind == "Convert":
            source = args[0].ty.kind
            target = ty.kind
            code = self.ex(args[0])
            if source in ("f32", "f64") and target in ("i32", "i64"):
                if source == "f32": code = "(f64.promote_f32 " + code + ")"
                return "(call $rt.f64_to_" + target + " " + code + ")"
            conversions = {"i32:i64": "i64.extend_i32_s", "i32:f32": "f32.convert_i32_s", "i32:f64": "f64.convert_i32_s", "i64:i32": "i32.wrap_i64", "i64:f32": "f32.convert_i64_s", "i64:f64": "f64.convert_i64_s", "f32:f64": "f64.promote_f32", "f64:f32": "f32.demote_f64", "bool:i32": "", "bool:i64": "i64.extend_i32_u", "bool:f32": "f32.convert_i32_u", "bool:f64": "f64.convert_i32_u"}
            op = conversions[source + ":" + target]
            return "(" + op + " " + code + ")" if op != "" else code
        if kind == "IsNull":
            code = "(ref.is_null " + self.ex(args[0]) + ")"
            return "(i32.eqz " + code + ")" if text == "not" else code
        if kind == "IsInstance": return "(ref.test (ref $" + text + ") " + self.ex(args[0]) + ")"
        if kind == "IfExpr": return f"(if (result {self.types.valtype(ty)}) {self.ex(args[0])} (then {self.ex(args[1])}) (else {self.ex(args[2])}))"
        if kind == "Call":
            if text.startswith("snek.lib."):
                operations = {"argv": "arguments", "getenv": "environment", "getenv-required": "environment", "time": "system-clock", "monotonic": "monotonic-clock", "sleep": "sleep", "token_bytes": "random", "token_hex": "random", "randbelow": "random"}
                feature = operations[text[9:]]
                if feature not in self.features: self.features.append(feature)
                if ty.kind == "list": self.types.heap(ty)
            if text.startswith("snek.sys."):
                if text[9:] not in self.io: self.io.append(text[9:])
                self.types.heap(DeclType("list").add(DeclType("i32")))
            if text in ("snek.read_bytes", "snek.write_bytes"):
                if self.component: raise ValueError("read_bytes/write_bytes currently require the core WASI target, not --component")
                self.byte_io = True
                self.types.heap(DeclType("list").add(DeclType("i32")))
            return self.call("$" + text, args)
        if kind == "New": return self.call("$" + text + ".__new__", args)
        if kind == "CallMethod":
            cls = self.checked.decl.classes[args[0].ty.name]
            method = cls.methods[text]
            owners: list[str] = []
            for name in self.checked.decl.class_order:
                if self.checked.decl.subclass(name, cls.name):
                    owner = self.checked.decl.classes[name].methods[text].owner
                    if owner not in owners: owners.append(owner)
            if len(owners) == 1: return self.call("$" + owners[0] + "." + text, args)
            values = ""
            for arg in args[1:]: values += " " + self.ex(arg)
            receiver = args[0]
            recv = ""
            again = ""
            if receiver.kind == "LocalGet" and receiver.ty.same(inner_type(receiver.declared)):
                recv = self.ex(receiver)
                again = "(local.get $" + receiver.text + ")"
            else:
                tmp = self.temp("(ref null $" + cls.name + ")")
                recv = f"(ref.as_non_null (local.tee {tmp} {self.ex(receiver)}))"
                again = "(local.get " + tmp + ")"
            return f"(call_ref ${method.decl}.{text}.sig {recv}{values} (struct.get ${cls.name}.vt ${text} (struct.get ${cls.name} $__vt__ {again})))"
        if kind == "StructNew":
            values = ""
            for arg in args: values += " " + self.ex(arg)
            return f"(struct.new ${text} (global.get ${text}.vt.inst){values})"
        if kind == "GetField": return f"(struct.get ${args[0].ty.name} ${text} {self.ex(args[0])})"
        if kind == "ClassRef": return f"(global.get ${text}.vt.inst)"
        if kind == "TypeOf": return f"(struct.get ${args[0].ty.name} $__vt__ {self.ex(args[0])})"
        if kind == "ClassName": return f"(struct.get ${args[0].ty.args[0].name}.vt $__name__ {self.ex(args[0])})"
        if kind == "TracebackToken": return "(struct.new $traceback<>)"
        if kind == "TypeName": return f"(struct.get ${text}.vt $__name__ (struct.get ${text} $__vt__ {self.ex(args[0])}))"
        if kind == "ListLit":
            self.types.heap(ty)
            name = emit_mangle(ty.args[0])
            values = ""
            for arg in args: values += " " + self.ex(arg)
            return f"(struct.new $list.{name} (i32.const {len(args)}) (array.new_fixed $arr.{name} {len(args)}{values}))"
        if kind == "TupleLit":
            heap = self.types.heap(ty)
            values_list: list[str] = []
            for arg in args: values_list.append(self.ex(arg))
            return f"(struct.new {heap} {' '.join(values_list)})"
        if kind == "TupleGet": return f"(struct.get {self.types.heap(args[0].ty)} {text} {self.ex(args[0])})"
        if kind == "DictLit":
            heap = self.types.heap(ty)
            tmp = self.temp("(ref null " + heap + ")")
            new = self.operation(ty, "new")
            put = self.operation(ty, "set")
            code_parts: list[str] = []
            pairs: list[str] = []
            for i in range(0, len(args), 2):
                kt = self.temp(self.types.storage(args[i].ty))
                vt = self.temp(self.types.storage(args[i + 1].ty))
                code_parts.append(f"(local.set {kt} {self.ex(args[i])})")
                code_parts.append(f"(local.set {vt} {self.ex(args[i + 1])})")
                pairs.append(self.local(args[i].ty, kt) + " " + self.local(args[i + 1].ty, vt))
            code_parts.append(f"(local.set {tmp} (call {new}))")
            for pair in pairs: code_parts.append(f"(call {put} (ref.as_non_null (local.get {tmp})) {pair})")
            return f"(block (result (ref {heap})) {' '.join(code_parts)} (ref.as_non_null (local.get {tmp})))"
        if kind in ("ListOp", "TupleOp", "DictOp"): return self.call(self.operation(args[0].ty, text), args)
        if kind == "KeyedListOp":
            name = "$list." + emit_mangle(args[0].ty.args[0]) + "." + text + ".key<" + emit_mangle(args[1].ty) + ">"
            self.types.heap(args[0].ty)
            self.types.heap(args[1].ty)
            self.keyed[name] = e
            return self.call(name, args)
        if kind in ("ListGet", "ListPop", "ListAppend"):
            self.types.heap(args[0].ty)
            op = "get" if kind == "ListGet" else "pop" if kind == "ListPop" else "append"
            return self.call("$list." + emit_mangle(args[0].ty.args[0]) + "." + op, args)
        if kind == "ListLen": return f"(struct.get {self.types.heap(args[0].ty)} $len {self.ex(args[0])})"
        if kind == "StrLen": return self.call("$rt.str_len", args)
        if kind == "StrConcat":
            parts: list[BodyExpr] = []
            stack: list[BodyExpr] = [e]
            fused = False
            while len(stack) > 0:
                part = stack.pop()
                if part.kind == "StrConcat": stack.extend([part.args[1], part.args[0]])
                else:
                    parts.append(part)
                    if part.kind == "ToStr" and part.args[0].ty.kind in ("i32", "i64"): fused = True
            if fused:
                shape = ""
                codes: list[str] = []
                for part in parts:
                    if part.kind == "ToStr" and part.args[0].ty.kind in ("i32", "i64"):
                        shape += "i"
                        code = self.ex(part.args[0])
                        codes.append("(i64.extend_i32_s " + code + ")" if part.args[0].ty.kind == "i32" else code)
                    else:
                        shape += "s"
                        codes.append(self.ex(part))
                if shape not in self.builders: self.builders.append(shape)
                return f"(call $str.build.{shape} {' '.join(codes)})"
            if len(parts) == 2: return self.call("$rt.str_concat", parts)
            values = ""
            for part in parts: values += " " + self.ex(part)
            return f"(call $rt.str_concat_n (array.new_fixed $strs {len(parts)}{values}))"
        if kind == "ToStr": return self.to_string(args[0].ty, self.ex(args[0]))
        if kind == "Seq":
            ret = " (result " + self.types.valtype(ty) + ")" if ty.kind != "None" else ""
            code = self.block(e.pre)
            return "(block" + ret + " " + code + " " + self.ex(args[0]) + ")"
        if kind == "MachineOp":
            heap = self.types.heap(args[0].ty)
            if text != "value": return self.call(heap + "." + text, args)
            code = "(struct.get " + heap + " $value " + self.ex(args[0]) + ")"
            return "(ref.as_non_null " + code + ")" if ty.reference() and ty.kind != "optional" else code
        raise ValueError("unknown owned emission expression " + kind)

    def jump(self, kind: str, value: str = "") -> str:
        jumps = {"return": 1, "break": 2, "continue": 3, "exit_inline": 4}
        for i in range(len(self.controls) - 1, -1, -1):
            ctl = self.controls[i]
            if ctl.kind == "finally":
                if kind not in ctl.used: ctl.used.append(kind)
                code = ""
                if kind == "return" and value != "":
                    if ctl.ret == "": ctl.ret = self.temp(self.types.storage(self.result))
                    code = "(local.set " + ctl.ret + " " + value + ") "
                return code + f"(local.set {ctl.pending} (i32.const {jumps[kind]})) (br {ctl.exit})"
            if ctl.kind == "loop" and kind in ("break", "continue"):
                return "(br " + (ctl.exit if kind == "break" else ctl.cont) + ")"
            if ctl.kind == "inline" and kind == "exit_inline": return "(br " + ctl.exit + ")"
        if kind != "return": raise ValueError("invalid emission control transfer " + kind)
        return "(return " + value + ")" if value != "" else "(return)"

    def block(self, body: list[BodyStmt]) -> str:
        parts: list[str] = []
        for stmt in body: parts.append(self.stmt(stmt))
        return " ".join(parts)

    def order_free(self, e: BodyExpr) -> bool:
        return self.order_free(e.args[0]) if e.kind == "ToStr" else e.kind in ("Const", "StrLit", "NoneLit", "LocalGet")

    def output(self, text: str) -> str:
        data = emit_utf8(text)
        if len(data) == 0: return ""
        if len(data) == 1: return f"(call $rt.out_byte (i32.const {data[0]}))"
        return "(call $rt.out_str " + self.literals.lit(text) + ")"

    def stmt(self, s: BodyStmt) -> str:
        kind = s.kind
        es = s.exprs
        if kind == "SetLocal": return f"(local.set ${s.name} {self.ex(es[0])})"
        if kind == "SetField": return f"(struct.set ${es[0].ty.name} ${s.name} {self.ex(es[0])} {self.ex(es[1])})"
        if kind == "SetIndex":
            self.types.heap(es[0].ty)
            return self.call("$list." + emit_mangle(es[0].ty.args[0]) + ".set", es)
        if kind in ("CellSet", "CellClear"):
            heap = self.types.heap(es[0].ty)
            cell = self.ex(es[0])
            if kind == "CellSet": return f"(struct.set {heap} $value {cell} {self.ex(es[1])}) (struct.set {heap} $bound {cell} (i32.const 1))"
            return f"(struct.set {heap} $bound {cell} (i32.const 0)) (struct.set {heap} $value {cell} {self.zero(es[0].ty.args[0])})"
        if kind == "ExprStmt":
            code = self.ex(es[0])
            return code if es[0].ty.kind == "None" else "(drop " + code + ")"
        if kind == "Return": return self.jump("return", self.ex(es[0]) if len(es) > 0 else "")
        if kind == "If":
            cond = self.ex(es[0])
            yes = self.block(s.body)
            no = " (else " + self.block(s.otherwise) + ")" if len(s.otherwise) > 0 else ""
            return f"(if {cond} (then {yes}){no})"
        if kind == "Loop":
            n = self.label()
            self.controls.append(EmitControl("loop", "$brk." + n, "$cont." + n))
            cond = "(br_if $brk." + n + " (i32.eqz " + self.ex(es[0]) + ")) " if len(es) > 0 else ""
            body = self.block(s.body)
            self.controls.pop()
            step = self.block(s.step)
            return f"(block $brk.{n} (loop $top.{n} {cond}(block $cont.{n} {body}) {step} (br $top.{n})))"
        if kind == "InlineBlock":
            n = self.label()
            self.controls.append(EmitControl("inline", "$inl." + n))
            body = self.block(s.body)
            self.controls.pop()
            return f"(block $inl.{n} {body})"
        if kind in ("Break", "Continue", "ExitInline"):
            return self.jump("exit_inline" if kind == "ExitInline" else kind.lower())
        if kind == "Raise": return "(throw $snek.exc " + self.ex(es[0]) + ")"
        if kind == "Reraise": return "(throw $snek.exc (ref.as_non_null (local.get $" + s.name + ")))"
        if kind == "Print":
            pre: list[str] = []
            codes: list[str] = []
            for e in es:
                if len(es) == 1 or self.order_free(e): codes.append(self.ex(e))
                else:
                    value = e.args[0] if e.kind == "ToStr" else e
                    tmp = self.temp(self.types.storage(value.ty))
                    pre.append("(local.set " + tmp + " " + self.ex(value) + ")")
                    read = self.local(value.ty, tmp)
                    codes.append(self.to_string(value.ty, read) if e.kind == "ToStr" else read)
            for i in range(len(codes)):
                if i > 0: pre.append(self.output(s.sep))
                pre.append("(call $rt.out_str " + codes[i] + ")")
            pre.append(self.output(s.end))
            return " ".join(pre)
        if kind in ("Try", "TryFinally"):
            if s.has_final or kind == "TryFinally": return self.try_finally(s)
            return self.try_except(s)
        raise ValueError("unknown owned emission statement " + kind)

    def try_except(self, s: BodyStmt) -> str:
        n = self.label()
        exc = "$" + s.name
        code = f"(block $try.{n} (local.set {exc} (block $catch.{n} (result (ref $BaseException)) (try_table (catch $snek.exc $catch.{n}) {self.block(s.body)}) {self.block(s.otherwise)} (br $try.{n})))"
        catch_all = False
        for h in s.handlers:
            body = ""
            if h.name != "":
                src = "(local.get " + exc + ")"
                heap = self.types.heap(h.declared)
                if h.name in self.nonnull: src = "(ref.cast (ref " + heap + ") " + src + ")"
                elif h.declared.kind != "class" or h.declared.name != "BaseException": src = "(ref.cast (ref null " + heap + ") " + src + ")"
                body = "(local.set $" + h.name + " " + src + ") "
            body += self.block(h.body) + " (br $try." + n + ")"
            if h.catches_all:
                code += " " + body
                catch_all = True
                break
            test = ""
            for cls in h.classes:
                item = "(ref.test (ref $" + cls + ") (local.get " + exc + "))"
                test = item if test == "" else "(i32.or " + test + " " + item + ")"
            code += " (if " + test + " (then " + body + "))"
        if not catch_all: code += " (throw $snek.exc (ref.as_non_null (local.get " + exc + ")))"
        return code + ")"

    def try_finally(self, s: BodyStmt) -> str:
        n = self.label()
        k = self.temp("i32")
        exc = self.temp("(ref null $BaseException)")
        ctl = EmitControl("finally", "$fin." + n, "", k)
        self.controls.append(ctl)
        body = self.try_except(s) if len(s.handlers) > 0 else self.block(s.body)
        self.controls.pop()
        code = f"(local.set {k} (i32.const 0)) (block $fin.{n} (local.set {exc} (block $fin.catch.{n} (result (ref $BaseException)) (try_table (catch $snek.exc $fin.catch.{n}) {body}) (br $fin.{n}))) (local.set {k} (i32.const -1))) "
        code += self.block(s.otherwise if s.kind == "TryFinally" else s.finalbody)
        code += f" (if (i32.eq (local.get {k}) (i32.const -1)) (then (throw $snek.exc (ref.as_non_null (local.get {exc})))))"
        kinds = ["return", "break", "continue", "exit_inline"]
        for i in range(len(kinds)):
            kind = kinds[i]
            if kind in ctl.used:
                value = self.local(self.result, ctl.ret) if kind == "return" and ctl.ret != "" else ""
                code += f" (if (i32.eq (local.get {k}) (i32.const {i + 1})) (then {self.jump(kind, value)}))"
        return code

    def function(self, f: BodyFunction) -> str:
        self.temps = []
        self.nonnull = []
        self.controls = []
        self.labels = 0
        self.result = f.sig.ret
        params = f.sig.params.copy()
        locals_ = f.locals.copy()
        signature = ""
        cast = ""
        if f.closure_type.kind != "None":
            signature = self.types.heap(f.closure_type)[1:] + ".sig"
            params.insert(0, DeclParam("closure.env", DeclType("Env"), missing()))
        if f.method_decl != "":
            signature = f.method_decl + "." + f.name.split(".", 1)[1] + ".sig"
            params.insert(0, DeclParam("self" if f.owner == f.method_decl else "self.in", DeclType("class", f.method_decl), missing()))
            if f.owner != f.method_decl:
                if "cell.self" not in locals_:
                    cast = f.owner
                    locals_["self"] = DeclType("class", f.owner)
                    self.nonnull.append("self")
                elif "self" in locals_: del locals_["self"]
        for param in params:
            if param.ty.reference() and param.ty.kind != "optional": self.nonnull.append(param.name)
        if f.machine: raise ValueError("machine function requires owned CFG integration")
        body = f"(local.set $self (ref.cast (ref ${cast}) (local.get $self.in))) " if cast != "" else ""
        body += self.block(f.body)
        if self.result.kind != "None" and (len(f.body) == 0 or f.body[-1].kind != "Return"): body += " (unreachable)"
        head = "  (func $" + f.name + (" (type $" + signature + ")" if signature != "" else "")
        for param in params: head += " (param $" + param.name + " " + self.types.valtype(param.ty) + ")"
        if self.result.kind != "None": head += " (result " + self.types.valtype(self.result) + ")"
        for name, ty in locals_.items():
            head += " (local $" + name + " " + (self.types.valtype(ty) if name == "self" and cast != "" else self.types.storage(ty)) + ")"
        return head + " " + " ".join(self.temps) + " " + body + ")"
