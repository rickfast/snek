"""Typed structured wait groups shared by both emitters."""
from __future__ import annotations
from bootstrap.parser import Node, string
from bootstrap.machine_emit import machine_fields_wat


def wait_all_code(name: str, heaps: list[str], reuse: str) -> str:
    B = "$coro<none>"
    F = name + ".frame"
    a = "(local.get $a)"
    f = "(local.get $f)"
    failure = "(struct.get " + F + " $failure " + f + ")"
    fields = machine_fields_wat(Node("Machine").put("heap", string(B)).put("elem", Node("Type").put("none", Node("scalar", "true"))))
    params = ""
    values = ""
    checks = ""
    claims = ""
    steps = ""
    clears = ""
    all_done = "(i32.const 1)"
    for i in range(len(heaps)):
        C = heaps[i]
        n = str(i)
        child = "(ref.as_non_null (struct.get " + F + " $child" + n + " " + f + "))"
        done = "(struct.get " + F + " $done" + n + " " + f + ")"
        set_done = "(struct.set " + F + " $done" + n + " " + f + " (i32.const 1))"
        fields += " (field $child" + n + " (mut (ref null " + C + "))) (field $done" + n + " (mut i32)) (field $cancel" + n + " (mut i32))"
        params += " (param $p" + n + " (ref " + C + "))"
        values += " (local.get $p" + n + ") (i32.const 0) (i32.const 0)"
        invalid = "(struct.get " + C + " $claimed " + child + ")"
        for j in range(i):
            if heaps[j] == C:
                other = "(struct.get " + F + " $child" + str(j) + " " + f + ")"
                invalid = "(i32.or " + invalid + " (ref.eq " + child + " " + other + "))"
        checks += "(if " + invalid + " (then (struct.set " + B + " $pc " + a + " (i32.const -1)) (struct.set " + B + " $busy " + a + " (i32.const 0)) (throw $snek.exc (call $RuntimeError.__new__ " + reuse + ")))) "
        claims += "(call " + C + ".claim " + child + ") "
        steps += "(if (i32.eqz " + done + ") (then (local.set $inject (ref.null $BaseException)) "
        steps += "(if (i32.and (i32.eqz (ref.is_null " + failure + ")) (i32.eqz (struct.get " + F + " $cancel" + n + " " + f + "))) (then (struct.set " + F + " $cancel" + n + " " + f + " (i32.const 1)) (local.set $inject (call $GeneratorExit.__new__ (ref.null $str))))) "
        steps += "(local.set $caught (block $caught (result (ref null $BaseException)) (try_table (catch $snek.exc $caught) (local.set $status (call " + C + ".resume " + child + " (local.get $inject))) (br $caught (ref.null $BaseException))) (unreachable))) "
        steps += "(if (ref.is_null (local.get $caught)) (then (if (i32.eqz (local.get $status)) (then " + set_done + "))) (else " + set_done
        steps += " (if (ref.is_null " + failure + ") (then (struct.set " + F + " $failure " + f + " (local.get $caught)) (global.set $sm.yielded (i32.const 1)))))))) "
        all_done = "(i32.and " + all_done + " " + done + ")"
        clears += "(struct.set " + F + " $child" + n + " " + f + " (ref.null " + C + ")) "
    fields += " (field $failure (mut (ref null $BaseException)))"
    return ("(type " + F + " (sub final " + B + " (struct " + fields + ")))\n"
        + "(elem declare func " + name + ".step)\n"
        + "(func " + name + params + " (result (ref " + B + ")) (struct.new " + F + " (i32.const 0) (i32.const 0) (i32.const -1) (i32.const 0) (i32.const 0) (ref.func " + name + ".step)" + values + " (ref.null $BaseException)))\n"
        + "(func " + name + ".step (type " + B + ".sig) (param $a (ref " + B + ")) (param $error (ref null $BaseException)) (result i32)"
        + " (local $f (ref " + F + ")) (local $caught (ref null $BaseException)) (local $inject (ref null $BaseException)) (local $status i32)"
        + " (local.set $f (ref.cast (ref " + F + ") " + a + "))"
        + " (if (i32.eqz (struct.get " + B + " $pc " + a + ")) (then " + checks + claims + " (struct.set " + B + " $pc " + a + " (i32.const 1))))"
        + " (if (i32.and (ref.is_null " + failure + ") (i32.eqz (ref.is_null (local.get $error)))) (then (struct.set " + F + " $failure " + f + " (local.get $error)))) "
        + steps + " (struct.set " + B + " $busy " + a + " (i32.const 0))"
        + " (if " + all_done + " (then (struct.set " + B + " $pc " + a + " (i32.const -1)) " + clears
        + " (if (i32.eqz (ref.is_null " + failure + ")) (then (throw $snek.exc (ref.as_non_null " + failure + ")))) (return (i32.const 0)))) (i32.const 1))")
