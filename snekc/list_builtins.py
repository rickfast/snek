"""WAT helpers for reductions and stable sorting of monomorphized lists."""

from .types import BOOL, F32, F64, I32, I64, STR, NUMERIC, OptionalT, Prim


def list_builtin_code(emitter, t, op: str) -> str:
    e = t.elem
    L = emitter.heap(t)
    A = "$arr." + L.removeprefix("$list.")
    R = emitter.valtype(e)
    get = "array.get_u" if e == BOOL else "array.get"

    def load(index: str, array: str = "$data") -> str:
        code = f"({get} {A} (local.get {array}) {index})"
        return f"(ref.as_non_null {code})" if e.is_ref else code

    def less(a: str, b: str) -> str:
        if e == STR:
            return f"(i32.lt_s (call $rt.str_cmp {a} {b}) (i32.const 0))"
        n = "i32" if e == BOOL else e.name
        cmp = "lt" if isinstance(e, Prim) and e.is_float else "lt_s"
        return f"({n}.{cmp} {a} {b})"

    head = f"  (func {L}.{op} (param $xs (ref {L}))"
    setup = (f"(local.set $n (struct.get {L} $len (local.get $xs)))\n"
             f"    (local.set $data (struct.get {L} $data (local.get $xs)))")
    if op.startswith(("min", "max")):
        kind = op.split("_")[0]
        optional = op.endswith("optional")
        result = emitter.valtype(OptionalT(e)) if optional else R
        default = "_" in op
        param = f" (param $fallback {result})" if default else ""
        empty = "(return (local.get $fallback))" if default else emitter.raise_code("ValueError", f"{kind}() iterable argument is empty")
        cmp = less("(local.get $value)", "(local.get $best)") if kind == "min" else less("(local.get $best)", "(local.get $value)")
        return head + f"""{param} (result {result})
    (local $n i32) (local $i i32) (local $data (ref {A})) (local $best {R}) (local $value {R})
    {setup}
    (if (i32.eqz (local.get $n)) (then {empty}))
    (local.set $best {load('(i32.const 0)')})
    (local.set $i (i32.const 1))
    (block $done (loop $next
      (br_if $done (i32.ge_u (local.get $i) (local.get $n)))
      (local.set $value {load('(local.get $i)')})
      (if {cmp} (then (local.set $best (local.get $value))))
      (local.set $i (i32.add (local.get $i) (i32.const 1))) (br $next)))
    (local.get $best))"""
    if op.startswith("sum_"):
        ret = NUMERIC[op.removeprefix("sum_")]
        N = ret.name
        value = load("(local.get $i)")
        source = I32 if e == BOOL else e
        if source != ret:
            conv = {(I32, I64): "i64.extend_i32_s", (I32, F64): "f64.convert_i32_s", (F32, F64): "f64.promote_f32"}[(source, ret)]
            value = f"({conv} {value})"
        compensated = e in (F32, F64)
        locals_ = "(local $c f64) (local $v f64) (local $next f64)" if compensated else ""
        add = f"(local.set $total ({N}.add (local.get $total) {value}))"
        if compensated:
            # Neumaier summation, matching CPython 3.13's float-only fast path.
            # https://github.com/python/cpython/blob/v3.13.0/Python/bltinmodule.c
            add = f"""(local.set $v {value})
      (local.set $next (f64.add (local.get $total) (local.get $v)))
      (local.set $c (f64.add (local.get $c)
        (if (result f64) (f64.ge (f64.abs (local.get $total)) (f64.abs (local.get $v)))
          (then (f64.add (f64.sub (local.get $total) (local.get $next)) (local.get $v)))
          (else (f64.add (f64.sub (local.get $v) (local.get $next)) (local.get $total))))))
      (local.set $total (local.get $next))"""
        finish = ("(if (i32.and (f64.ne (local.get $c) (f64.const 0)) "
                  "(f64.lt (f64.abs (local.get $c)) (f64.const inf))) "
                  "(then (local.set $total (f64.add (local.get $total) (local.get $c)))))") if compensated else ""
        return head + f""" (param $total {N}) (result {N})
    (local $n i32) (local $i i32) (local $data (ref {A})) {locals_}
    {setup}
    (block $done (loop $next_item
      (br_if $done (i32.ge_u (local.get $i) (local.get $n)))
      {add}
      (local.set $i (i32.add (local.get $i) (i32.const 1))) (br $next_item)))
    {finish} (local.get $total))"""
    assert op == "sorted", op
    # Bottom-up merge sort: O(n log n), one scratch array, no recursion. Choosing
    # the left run on ties preserves stability, also with reverse=True.
    left, right = load("(local.get $i)", "$src"), load("(local.get $j)", "$src")
    choose_right = (f"(if (result i32) (local.get $reverse) (then {less(left, right)}) "
                    f"(else {less(right, left)}))")
    return head + f""" (param $reverse i32) (result (ref {L}))
    (local $n i32) (local $data (ref {A})) (local $src (ref {A})) (local $dst (ref {A})) (local $swap (ref {A}))
    (local $width i32) (local $lo i32) (local $mid i32) (local $hi i32) (local $i i32) (local $j i32) (local $out i32)
    {setup}
    (local.set $src (array.new_default {A} (local.get $n)))
    (array.copy {A} {A} (local.get $src) (i32.const 0) (local.get $data) (i32.const 0) (local.get $n))
    (local.set $dst (array.new_default {A} (local.get $n)))
    (local.set $width (i32.const 1))
    (block $done (loop $pass
      (br_if $done (i32.ge_u (local.get $width) (local.get $n)))
      (local.set $lo (i32.const 0))
      (block $pass_done (loop $runs
        (br_if $pass_done (i32.ge_u (local.get $lo) (local.get $n)))
        (local.set $mid (i32.add (local.get $lo) (local.get $width)))
        (if (i32.gt_u (local.get $mid) (local.get $n)) (then (local.set $mid (local.get $n))))
        (local.set $hi (i32.add (local.get $mid) (local.get $width)))
        (if (i32.gt_u (local.get $hi) (local.get $n)) (then (local.set $hi (local.get $n))))
        (local.set $i (local.get $lo)) (local.set $j (local.get $mid)) (local.set $out (local.get $lo))
        (block $merged (loop $merge
          (br_if $merged (i32.ge_u (local.get $out) (local.get $hi)))
          (if (if (result i32) (i32.ge_u (local.get $j) (local.get $hi)) (then (i32.const 0))
                (else (if (result i32) (i32.ge_u (local.get $i) (local.get $mid)) (then (i32.const 1))
                  (else {choose_right}))))
            (then (array.set {A} (local.get $dst) (local.get $out) {right})
              (local.set $j (i32.add (local.get $j) (i32.const 1))))
            (else (array.set {A} (local.get $dst) (local.get $out) {left})
              (local.set $i (i32.add (local.get $i) (i32.const 1)))))
          (local.set $out (i32.add (local.get $out) (i32.const 1))) (br $merge)))
        (local.set $lo (local.get $hi)) (br $runs)))
      (local.set $swap (local.get $src)) (local.set $src (local.get $dst)) (local.set $dst (local.get $swap))
      (local.set $width (i32.shl (local.get $width) (i32.const 1))) (br $pass)))
    (struct.new {L} (local.get $n) (local.get $src)))"""
