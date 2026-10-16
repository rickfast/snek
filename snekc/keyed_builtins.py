"""Keyed list sorting/reductions. Keys stay unboxed in typed parallel arrays."""
from .types import BOOL, STR, ListT, OptionalT, Prim


def keyed_builtin_code(emitter, name, elem, key, op, result):
    L = emitter.heap(ListT(elem))
    A = "$arr." + L.removeprefix("$list.")
    K = key.ret
    KL = emitter.heap(ListT(K))
    KA = "$arr." + KL.removeprefix("$list.")
    C = emitter.heap(key)
    V, R = emitter.valtype(elem), emitter.valtype(K)

    def load(ty, arr_type, arr, index):
        code = f"({'array.get_u' if ty == BOOL else 'array.get'} {arr_type} {arr} {index})"
        return f"(ref.as_non_null {code})" if ty.is_ref and not isinstance(ty, OptionalT) else code

    def value(arr, index):
        return load(elem, A, f"(local.get {arr})", index)

    def key_at(arr, index):
        return load(K, KA, f"(local.get {arr})", index)

    def call_key(value):
        return (f"(call_ref {C}.sig (struct.get {C} $env (local.get $key)) {value} "
                f"(struct.get {C} $fn (local.get $key)))")

    def less(a, b):
        if K == STR:
            return f"(i32.lt_s (call $rt.str_cmp {a} {b}) (i32.const 0))"
        n = "i32" if K == BOOL else K.name
        cmp = "lt" if isinstance(K, Prim) and K.is_float else "lt_s"
        return f"({n}.{cmp} {a} {b})"

    head = f"  (func {name} (param $xs (ref {L})) (param $key (ref {C}))"
    if op != "sorted":
        kind = op.split("_")[0]
        ret = emitter.valtype(result)
        param = f" (param $fallback {ret})" if "_" in op else ""
        empty = ("(return (local.get $fallback))" if param else
                 emitter.raise_code("ValueError", f"{kind}() iterable argument is empty"))
        length = f"(struct.get {L} $len (local.get $xs))"
        data = f"(struct.get {L} $data (local.get $xs))"
        item = load(elem, A, data, "(local.get $i)")
        cmp = (less("(local.get $candidate)", "(local.get $best_key)") if kind == "min" else
               less("(local.get $best_key)", "(local.get $candidate)"))
        # Reload length and backing storage after every callback. A list iterator
        # observes appends, removals, replacements and backing-array growth.
        return head + f"""{param} (result {ret})
    (local $i i32) (local $best {V}) (local $value {V}) (local $best_key {R}) (local $candidate {R})
    (if (i32.eqz {length}) (then {empty}))
    (local.set $best {item})
    (local.set $best_key {call_key('(local.get $best)')})
    (local.set $i (i32.const 1))
    (block $done (loop $next
      (br_if $done (i32.ge_u (local.get $i) {length}))
      (local.set $value {item})
      (local.set $candidate {call_key('(local.get $value)')})
      (if {cmp} (then (local.set $best (local.get $value)) (local.set $best_key (local.get $candidate))))
      (local.set $i (i32.add (local.get $i) (i32.const 1))) (br $next)))
    (local.get $best))"""

    left = value("$src", "(local.get $i)")
    right = value("$src", "(local.get $j)")
    lk = key_at("$keys", "(local.get $i)")
    rk = key_at("$keys", "(local.get $j)")
    choose_right = (f"(if (result i32) (local.get $reverse) (then {less(lk, rk)}) "
                    f"(else {less(rk, lk)}))")
    # Copy the input before callbacks; collect each key exactly once, left to
    # right. Merge values and cached keys together, taking the left run on ties.
    return head + f""" (param $reverse i32) (result (ref {L}))
    (local $n i32) (local $src (ref {A})) (local $dst (ref {A})) (local $swap (ref {A}))
    (local $keys (ref {KA})) (local $scratch (ref {KA})) (local $key_swap (ref {KA}))
    (local $width i32) (local $lo i32) (local $mid i32) (local $hi i32) (local $i i32) (local $j i32) (local $out i32)
    (local.set $n (struct.get {L} $len (local.get $xs)))
    (local.set $src (array.new_default {A} (local.get $n)))
    (array.copy {A} {A} (local.get $src) (i32.const 0) (struct.get {L} $data (local.get $xs)) (i32.const 0) (local.get $n))
    (local.set $keys (array.new_default {KA} (local.get $n)))
    (block $keyed (loop $key_next
      (br_if $keyed (i32.ge_u (local.get $i) (local.get $n)))
      (array.set {KA} (local.get $keys) (local.get $i) {call_key(left)})
      (local.set $i (i32.add (local.get $i) (i32.const 1))) (br $key_next)))
    (if (i32.lt_u (local.get $n) (i32.const 2)) (then (return (struct.new {L} (local.get $n) (local.get $src)))))
    (local.set $dst (array.new_default {A} (local.get $n)))
    (local.set $scratch (array.new_default {KA} (local.get $n)))
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
              (array.set {KA} (local.get $scratch) (local.get $out) {rk})
              (local.set $j (i32.add (local.get $j) (i32.const 1))))
            (else (array.set {A} (local.get $dst) (local.get $out) {left})
              (array.set {KA} (local.get $scratch) (local.get $out) {lk})
              (local.set $i (i32.add (local.get $i) (i32.const 1)))))
          (local.set $out (i32.add (local.get $out) (i32.const 1))) (br $merge)))
        (local.set $lo (local.get $hi)) (br $runs)))
      (local.set $swap (local.get $src)) (local.set $src (local.get $dst)) (local.set $dst (local.get $swap))
      (local.set $key_swap (local.get $keys)) (local.set $keys (local.get $scratch)) (local.set $scratch (local.get $key_swap))
      (local.set $width (i32.shl (local.get $width) (i32.const 1))) (br $pass)))
    (struct.new {L} (local.get $n) (local.get $src)))"""
