"""Snek-owned suspension frame, dispatch, factory and protocol WAT emission.

The temporary host adapter supplies ordinary expression/statement fragments
and resolved Wasm type names. All suspension-specific WAT is assembled here.
"""
from __future__ import annotations
from snek import i32
from bootstrap.parser import Node


def machine_flag(node: Node, key: str) -> bool:
    return node.get(key).text == "true"


def machine_default(ty: Node) -> str:
    if machine_flag(ty, "ref"):
        return "(ref.null " + ty.get("heap").text + ")"
    return "(" + ty.get("value").text + ".const 0)"


def machine_value_wat(machine: Node, code: str) -> str:
    value = "(struct.get " + machine.get("heap").text + " $value " + code + ")"
    elem = machine.get("elem")
    if machine_flag(elem, "ref") and not machine_flag(elem, "optional"):
        return "(ref.as_non_null " + value + ")"
    return value


def machine_fields_wat(machine: Node) -> str:
    value = ""
    elem = machine.get("elem")
    if not machine_flag(elem, "none"):
        value = " (field $value (mut " + elem.get("storage").text + "))"
    B = machine.get("heap").text
    return "(field $pc (mut i32)) (field $busy (mut i32)) (field $handler (mut i32)) (field $claimed (mut i32)) (field $started (mut i32)) (field $step (ref " + B + ".sig))" + value


def machine_types_wat(request: Node) -> str:
    out: list[str] = []
    for machine in request.get("machines").items:
        B = machine.get("heap").text
        out.append(f"    (type {B}.sig (func (param (ref {B}) (ref null $BaseException)) (result i32)))")
        out.append(f"    (type {B} (sub (struct {machine_fields_wat(machine)})))")
    for frame in request.get("frames").items:
        machine = frame.get("machine")
        B = machine.get("heap").text
        F = "$frame." + frame.get("name").text
        fields: list[str] = []
        for local in frame.get("locals").items:
            name = local.get("name").text
            storage = local.get("type").get("storage").text
            fields.append(f"(field $v.{name} (mut {storage}))")
        fields_ = " ".join(fields)
        out.append(f"    (type {F} (sub final {B} (struct {machine_fields_wat(machine)} {fields_})))")
    return "\n".join(out)


def machine_setpc(pc: str) -> str:
    return "(local.set $sm.pc (i32.const " + pc + "))"


def machine_goto(pc: str) -> str:
    return machine_setpc(pc) + " (br $sm.dispatch)"


def machine_frame_wat(request: Node) -> str:
    name = request.get("name").text
    machine = request.get("machine")
    B = machine.get("heap").text
    F = "$frame." + name
    frame = "(local.get $sm.frame)"
    base = "(local.get $sm.base)"
    saves: list[str] = []
    clears: list[str] = []
    loads_: list[str] = []
    local_decls: list[str] = []
    vals_: list[str] = []
    param_names: list[str] = []
    params_: list[str] = []
    for param in request.get("params").items:
        n = param.get("name").text
        param_names.append(n)
        param_type = param.get("type").get("value").text
        params_.append(f"(param ${n} {param_type})")
    for local in request.get("locals").items:
        n = local.get("name").text
        ty = local.get("type")
        saves.append(f"(struct.set {F} $v.{n} {frame} (local.get ${n}))")
        if machine_flag(ty, "ref"):
            heap = ty.get("heap").text
            clears.append(f"(struct.set {F} $v.{n} {frame} (ref.null {heap}))")
        loads_.append(f"(local.set ${n} (struct.get {F} $v.{n} {frame}))")
        storage = ty.get("storage").text
        local_decls.append(f"(local ${n} {storage})")
        vals_.append(f"(local.get ${n})" if n in param_names else machine_default(ty))
    save = " ".join(saves)
    clear = " ".join(clears)
    elem = machine.get("elem")
    if machine_flag(machine, "generator") and machine_flag(elem, "ref"):
        heap = elem.get("heap").text
        clear += f" (struct.set {B} $value {base} (ref.null {heap}))"
    finish = f"(struct.set {B} $pc {base} (i32.const -1)) (struct.set {B} $busy {base} (i32.const 0)) {clear}"
    cases: list[str] = []
    isawait = "(i32.const 0)"
    states = request.get("states").items
    for i in range(len(states)):
        state = states[i]
        error = state.get("error").text
        next_ = state.get("next").text
        kind = state.get("kind").text
        code = f"(local.set $sm.handler (i32.const {error})) "
        if kind == "done":
            if machine_flag(request, "result"):
                code += f"(struct.set {B} $value {base} (local.get $sm.result)) "
            code += f"{finish} (return (i32.const 0))"
        elif kind == "fail":
            code += finish
            message = request.get("stop_message").text
            code += f" (if (i32.and (i32.eqz (local.get $sm.unstarted)) (ref.test (ref $StopIteration) (local.get $sm.exc))) (then (throw $snek.exc (call $RuntimeError.__new__ {message}))))"
            code += " (throw $snek.exc (ref.as_non_null (local.get $sm.exc)))"
        elif kind == "branch":
            cond = state.get("code").text
            other = state.get("other").text
            code += f"(if {cond} (then {machine_setpc(next_)}) (else {machine_setpc(other)})) (br $sm.dispatch)"
        elif kind == "yield" or kind == "await":
            resume = next_
            if kind == "yield" and not machine_flag(machine, "generator"):
                code += "(global.set $sm.yielded (i32.const 1)) "
            if kind == "await":
                child_type = state.get("child_type")
                C = child_type.get("heap").text
                child = state.get("code").text
                code += f"(if (i32.eqz (call {C}.resume {child} (local.get $sm.inject))) (then "
                code += "(local.set $sm.inject (ref.null $BaseException)) "
                target = state.get("target").text
                if target != "":
                    value = machine_value_wat(child_type, child)
                    code += f"(local.set ${target} {value}) "
                code += machine_goto(next_) + ")) "
                resume = str(i)
                isawait = f"(i32.or {isawait} (i32.eq (local.get $sm.pc) (i32.const {i})))"
            elif state.get("code").text != "":
                value = state.get("code").text
                code += f"(struct.set {B} $value {base} {value}) "
            code += f"{save} (struct.set {B} $pc {base} (i32.const {resume})) (struct.set {B} $handler {base} (i32.const {error})) (struct.set {B} $busy {base} (i32.const 0)) (return (i32.const 1))"
        elif kind == "stmt" or kind == "goto":
            if machine_flag(state, "statement"):
                code += state.get("code").text + " "
            code += machine_goto(next_)
        else:
            raise ValueError("unknown frame state kind")
        cases.append(f"(if (i32.eq (local.get $sm.pc) (i32.const {i})) (then {code}))")
    loads = " ".join(loads_)
    self_cast = request.get("self_cast").text
    if self_cast != "":
        loads += f" (local.set $self (ref.cast (ref ${self_cast}) (local.get $self.in)))"
    temps: list[str] = []
    for temp in request.get("temps").items:
        n = temp.get("name").text
        temp_type = temp.get("type").text
        temps.append(f"(local {n} {temp_type})")
    locals_ = " ".join(local_decls) + " " + " ".join(temps)
    fail = i32(request.get("fail").text)
    fail_code = cases[fail]
    cases[fail] = ""
    dispatch = " ".join(cases)
    step = f"""(func ${name}.step (type {B}.sig)
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
                  {dispatch} (unreachable)) (unreachable)))
            (local.set $sm.inject (ref.null $BaseException))
            (local.set $sm.pc (local.get $sm.handler)) (br $sm.dispatch)) (unreachable))"""
    value = "" if machine_flag(elem, "none") else " " + machine_default(elem)
    sig = request.get("signature").text
    if sig != "": sig = " (type $" + sig + ")"
    params = " ".join(params_)
    vals = " ".join(vals_)
    entry = request.get("entry").text
    factory = f"(func ${name}{sig} {params} (result (ref {B})) (struct.new {F} (i32.const {entry}) (i32.const 0) (i32.const {fail}) (i32.const 0) (i32.const 0) (ref.func ${name}.step){value} {vals}))"
    return f"(elem declare func ${name}.step)\n{factory}\n{step}"


def machine_error_wat(cls: str, literal: str) -> str:
    return f"(throw $snek.exc (call ${cls}.__new__ {literal}))"


def machine_helpers_wat(request: Node) -> str:
    out: list[str] = ["(func $sm.noop)"]
    for machine in request.get("machines").items:
        B = machine.get("heap").text
        a = "(local.get $a)"
        null = "(ref.null $BaseException)"
        generator = machine_flag(machine, "generator")
        error = machine_error_wat("ValueError" if generator else "RuntimeError", machine.get("busy_message").text)
        out.append(f"""(func {B}.resume (param $a (ref {B})) (param $e (ref null $BaseException)) (result i32)
              (if (struct.get {B} $busy {a}) (then {error}))
              (if (i32.eq (struct.get {B} $pc {a}) (i32.const -1))
                (then (if (i32.eqz (ref.is_null (local.get $e))) (then (throw $snek.exc (ref.as_non_null (local.get $e))))) (return (i32.const 0))))
              (struct.set {B} $busy {a} (i32.const 1))
              (call_ref {B}.sig {a} (local.get $e) (struct.get {B} $step {a})))
              (func {B}.advance (param $a (ref {B})) (result i32) (call {B}.resume {a} {null}))""")
        if not generator:
            error = machine_error_wat("RuntimeError", machine.get("reuse_message").text)
            out.append(f"""(func {B}.claim (param $a (ref {B}))
                  (if (struct.get {B} $claimed {a}) (then {error}))
                  (struct.set {B} $claimed {a} (i32.const 1)))""")
            continue
        value = machine_value_wat(machine, a)
        R = machine.get("elem").get("value").text
        error = machine_error_wat("RuntimeError", machine.get("close_message").text)
        out.append(f"""(func {B}.next (param $a (ref {B})) (result {R})
                (if (i32.eqz (call {B}.advance {a})) (then (throw $snek.exc (call $StopIteration.__new__ (ref.null $str))))) {value})""")
        out.append(f"""(func {B}.next_default (param $a (ref {B})) (param $d {R}) (result {R})
                (if (result {R}) (call {B}.advance {a}) (then {value}) (else (local.get $d))))""")
        out.append(f"""(func {B}.throw (param $a (ref {B})) (param $e (ref $BaseException)) (result {R})
                (if (i32.eqz (call {B}.resume {a} (local.get $e))) (then (throw $snek.exc (call $StopIteration.__new__ (ref.null $str))))) {value})""")
        out.append(f"""(func {B}.close (param $a (ref {B})) (local $e (ref $BaseException))
                (local.set $e (block $caught (result (ref $BaseException))
                  (try_table (catch $snek.exc $caught)
                    (if (call {B}.resume {a} (call $GeneratorExit.__new__ (ref.null $str)))
                      (then {error})) (return)) (unreachable)))
                (if (i32.eqz (i32.or (ref.test (ref $GeneratorExit) (local.get $e)) (ref.test (ref $StopIteration) (local.get $e)))) (then (throw $snek.exc (local.get $e)))))""")
    return "\n".join(out)


def machine_emit_wat(request: Node) -> str:
    if request.get("version").text != "1":
        raise ValueError("unsupported machine emission version")
    if request.kind == "FrameRequest": return machine_frame_wat(request)
    if request.kind == "MachineTypesRequest": return machine_types_wat(request)
    if request.kind == "MachineHelpersRequest": return machine_helpers_wat(request)
    raise ValueError("unknown machine emission request")
