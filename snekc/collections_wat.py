"""Monomorphized tuple and insertion-ordered dictionary runtimes.

Lookup uses open addressing; slots contain entry indices plus one (0 is empty,
-1 is deleted). A separate entry array preserves insertion order. Deletion
clears the entry reference, so keys and values can be collected. Rehashing
compacts vacant entries when no iterator is active. Loops release their iterator
in a finally block, including on break, return, and exceptions.
"""

from __future__ import annotations

from .types import BOOL, I32, I64, STR, DictT, DictViewT, ListT, OptionalT, TupleT, Type, strip_optional


class CollectionEmitter:
    def local_value(self, t: Type, name: str) -> str:
        code = f"(local.get {name})"
        return f"(ref.as_non_null {code})" if t.is_ref and not isinstance(t, OptionalT) else code

    def close_collection_types(self) -> None:
        while True:
            before = (len(self.list_types), len(self.tuple_types), len(self.dict_types), len(self.view_types))
            self.close_list_types()
            for t in list(self.tuple_types.values()):
                for e in t.elems:
                    self.valtype(e)
            for t in list(self.dict_types.values()):
                self.valtype(t.key)
                self.valtype(t.value)
            for t in list(self.view_types.values()):
                self.heap(t.mapping)
                self.valtype(t.elem)
            if before == (len(self.list_types), len(self.tuple_types), len(self.dict_types), len(self.view_types)):
                return

    def collection_types(self) -> list[str]:
        self.close_collection_types()
        out = []
        for m, t in self.tuple_types.items():
            fields = " ".join(f"(field {self.valtype(e)})" for e in t.elems)
            out.append(f"    (type ${m} (struct {fields}))")
        for m, t in self.dict_types.items():
            out += [
                f"    (type ${m}.entry (struct (field $key {self.valtype(t.key)})"
                f" (field $value (mut {self.valtype(t.value)}))))",
                f"    (type ${m}.entries (array (mut (ref null ${m}.entry))))",
                f"    (type ${m} (struct (field $len (mut i32)) (field $used (mut i32))"
                f" (field $slots (mut (ref $u32s))) (field $entries (mut (ref ${m}.entries))) (field $iters (mut i32))))",
            ]
        for m, t in self.view_types.items():
            out.append(f"    (type ${m} (struct (field $dict (ref {self.heap(t.mapping)}))))")
        return out

    def collection_op(self, t: Type, op: str) -> str:
        t = strip_optional(t)
        name = self.heap(t)
        ops = self.collection_ops.setdefault(name[1:], set())
        if isinstance(t, DictT):
            ops.add("core")
            if op not in ("new", "find", "grow", "set"):
                ops.add(op)
        else:
            ops.add(op)
        return f"{name}.{op}"

    def optional_repr_code(self, t: OptionalT, code: str) -> str:
        name = self.heap(t.inner)[1:]
        self.optional_reprs[name] = t.inner
        return f"(call $optional.{name}.repr {code})"

    def collection_helpers(self) -> str:
        """One worklist closes dependencies across all three collection families."""
        out, done = [], set()
        while True:
            self.close_collection_types()
            tasks = []
            for m, t in list(self.list_types.items()):
                tasks.append((("list", m, "basic"), lambda m=m, t=t: self.list_helper(m, t)))
                for op in sorted(self.list_ops.get(m, ())):
                    tasks.append((("list", m, op), lambda m=m, t=t, op=op: self.list_op_code(m, t, op)))
            for registry, helper in ((self.tuple_types, self.tuple_helper),
                                     (self.dict_types, self.dict_helper), (self.view_types, self.view_helper)):
                for m, t in list(registry.items()):
                    for op in sorted(self.collection_ops.get(m, ())):
                        tasks.append((("collection", m, op), lambda t=t, op=op, helper=helper: helper(t, op)))
            for name, inner in list(self.optional_reprs.items()):
                tasks.append((("optional", name), lambda name=name, inner=inner:
                    f"  (func $optional.{name}.repr (param $v (ref null ${name})) (result (ref $str))\n"
                    f"    (if (result (ref $str)) (ref.is_null (local.get $v)) (then {self.lit('None')})\n"
                    f"      (else {self.repr_code(inner, '(ref.as_non_null (local.get $v))')})))"))
            pending = [(key, generate) for key, generate in tasks if key not in done]
            if not pending:
                return "\n".join(out)
            for key, generate in pending:
                done.add(key)
                out.append(generate())

    def hash_code(self, t: Type, value: str) -> str:
        if isinstance(t, OptionalT):
            return (f"(if (result i32) (ref.is_null {value}) (then (i32.const 0)) "
                    f"(else {self.hash_code(t.inner, f'(ref.as_non_null {value})')}))")
        if t == STR:
            return f"(call $rt.str_hash {value})"
        if t in (BOOL, I32):
            return f"(i32.mul {value} (i32.const -1640531535))"
        if t == I64:
            return (f"(i32.mul (i32.xor (i32.wrap_i64 {value}) "
                    f"(i32.wrap_i64 (i64.shr_u {value} (i64.const 32)))) (i32.const -1640531535))")
        if isinstance(t, TupleT):
            return f"(call {self.collection_op(t, 'hash')} {value})"
        raise AssertionError(f"hash of {t}")

    def tuple_helper(self, t: TupleT, op: str) -> str:
        T = self.heap(t)
        head = f"  (func {T}.{op}"
        field = lambda i, obj='$v': f"(struct.get {T} {i} (local.get {obj}))"
        if op == "eq":
            tests = [f"(if (i32.eqz {self.eq_code(e, field(i, '$x'), field(i, '$y'))}) "
                     "(then (return (i32.const 0))))" for i, e in enumerate(t.elems)]
            return head + f""" (param $a (ref null {T})) (param $b (ref null {T})) (result i32)
    (local $x (ref {T})) (local $y (ref {T}))
    (if (ref.eq (local.get $a) (local.get $b)) (then (return (i32.const 1))))
    (if (i32.or (ref.is_null (local.get $a)) (ref.is_null (local.get $b))) (then (return (i32.const 0))))
    (local.set $x (ref.as_non_null (local.get $a)))
    (local.set $y (ref.as_non_null (local.get $b)))
    {' '.join(tests)}
    (i32.const 1))"""
        if op == "hash":
            mixes = [f"(local.set $h (i32.mul (i32.xor (local.get $h) {self.hash_code(e, field(i))}) "
                     "(i32.const 16777619)))" for i, e in enumerate(t.elems)]
            return head + f""" (param $v (ref {T})) (result i32)
    (local $h i32) (local.set $h (i32.const -2128831035))
    {' '.join(mixes)} (local.get $h))"""
        if op == "to_str":
            parts = [self.lit("(")]
            for i, e in enumerate(t.elems):
                if i:
                    parts.append(self.lit(", "))
                parts.append(self.repr_code(e, field(i)))
            parts.append(self.lit(",)" if len(t.elems) == 1 else ")"))
            return (head + f" (param $v (ref {T})) (result (ref $str))\n"
                    f"    (call $rt.str_concat_n (array.new_fixed $strs {len(parts)} {' '.join(parts)})))")
        e = t.elems[0]
        if op == "get":
            choices = [f"(if (i32.eq (local.get $i) (i32.const {i})) (then (return {field(i)})))"
                       for i in range(len(t.elems))]
            return head + f""" (param $v (ref {T})) (param $i i32) (result {self.valtype(e)})
    (if (i32.lt_s (local.get $i) (i32.const 0))
      (then (local.set $i (i32.add (local.get $i) (i32.const {len(t.elems)})))))
    {' '.join(choices)}
    {self.raise_code('IndexError', 'tuple index out of range')})"""
        if op in ("contains", "count", "index"):
            params = " (param $start i32) (param $stop i32)" if op == "index" else ""
            body = ["(local $n i32)"] if op == "count" else []
            if op == "index":
                for p in ("start", "stop"):
                    body.append(f"(if (i32.lt_s (local.get ${p}) (i32.const 0)) (then "
                                f"(local.set ${p} (i32.add (local.get ${p}) (i32.const {len(t.elems)})))))")
            for i in range(len(t.elems)):
                eq = self.eq_code(e, field(i), "(local.get $x)")
                if op == "index":
                    eq = (f"(if (result i32) (i32.and (i32.ge_s (i32.const {i}) (local.get $start)) "
                          f"(i32.lt_s (i32.const {i}) (local.get $stop))) (then {eq}) (else (i32.const 0)))")
                action = ("(local.set $n (i32.add (local.get $n) (i32.const 1)))" if op == "count"
                          else f"(return (i32.const {i if op == 'index' else 1}))")
                body.append(f"(if {eq} (then {action}))")
            body.append("(local.get $n)" if op == "count" else "(i32.const 0)" if op == "contains"
                        else self.raise_code("ValueError", "tuple.index(x): x not in tuple"))
            return head + f" (param $v (ref {T})) (param $x {self.valtype(e)}){params} (result i32)\n    " + "\n    ".join(body) + ")"
        raise AssertionError(f"tuple helper {op}")

    def dict_core(self, t: DictT) -> str:
        D = self.heap(t)
        E, A = D + ".entry", D + ".entries"
        K, V = self.valtype(t.key), self.valtype(t.value)
        key_hash = self.hash_code(t.key, "(local.get $k)")
        same = self.eq_code(t.key, f"(struct.get {E} $key (local.get $e))", "(local.get $k)")
        return f"""  (func {D}.new (result (ref {D}))
    (struct.new {D} (i32.const 0) (i32.const 0)
      (array.new_default $u32s (i32.const 8)) (array.new_default {A} (i32.const 4)) (i32.const 0)))
  (func {D}.find (param $d (ref {D})) (param $k {K}) (result i32)
    (local $slots (ref $u32s)) (local $mask i32) (local $p i32) (local $n i32) (local $free i32)
    (local $e (ref {E}))
    (local.set $slots (struct.get {D} $slots (local.get $d)))
    (local.set $mask (i32.sub (array.len (local.get $slots)) (i32.const 1)))
    (local.set $p (i32.and {key_hash} (local.get $mask)))
    (local.set $free (i32.const -1))
    (loop $probe
      (local.set $n (array.get $u32s (local.get $slots) (local.get $p)))
      (if (i32.eqz (local.get $n))
        (then (return (select (local.get $free) (local.get $p) (i32.ge_s (local.get $free) (i32.const 0))))))
      (if (i32.eq (local.get $n) (i32.const -1))
        (then (if (i32.lt_s (local.get $free) (i32.const 0)) (then (local.set $free (local.get $p)))))
        (else
          (local.set $e (ref.as_non_null (array.get {A} (struct.get {D} $entries (local.get $d))
            (i32.sub (local.get $n) (i32.const 1)))))
          (if {same} (then (return (local.get $p))))))
      (local.set $p (i32.and (i32.add (local.get $p) (i32.const 1)) (local.get $mask)))
      (br $probe))
    (unreachable))
  (func {D}.grow (param $d (ref {D}))
    (local $old (ref {A})) (local $entries (ref {A})) (local $slots (ref $u32s))
    (local $i i32) (local $j i32) (local $n i32) (local $p i32) (local $cap i32) (local $compact i32)
    (local $e (ref null {E}))
    (local.set $old (struct.get {D} $entries (local.get $d)))
    (local.set $n (struct.get {D} $used (local.get $d)))
    (local.set $compact (i32.eqz (struct.get {D} $iters (local.get $d))))
    (local.set $cap (array.len (local.get $old)))
    (if (i32.or (i32.eqz (local.get $compact))
          (i32.ge_u (struct.get {D} $len (local.get $d)) (i32.shr_u (local.get $cap) (i32.const 1))))
      (then (local.set $cap (i32.shl (local.get $cap) (i32.const 1)))))
    (local.set $entries (array.new_default {A} (local.get $cap)))
    (local.set $slots (array.new_default $u32s (i32.shl (array.len (local.get $entries)) (i32.const 1))))
    (struct.set {D} $entries (local.get $d) (local.get $entries))
    (struct.set {D} $slots (local.get $d) (local.get $slots))
    (block $done (loop $next
      (br_if $done (i32.ge_u (local.get $i) (local.get $n)))
      (local.set $e (array.get {A} (local.get $old) (local.get $i)))
      (if (i32.eqz (ref.is_null (local.get $e))) (then
        (if (i32.eqz (local.get $compact)) (then (local.set $j (local.get $i))))
        (array.set {A} (local.get $entries) (local.get $j) (local.get $e))
        (local.set $p (call {D}.find (local.get $d) (struct.get {E} $key (ref.as_non_null (local.get $e)))))
        (array.set $u32s (local.get $slots) (local.get $p) (i32.add (local.get $j) (i32.const 1)))
        (local.set $j (i32.add (local.get $j) (i32.const 1)))))
      (local.set $i (i32.add (local.get $i) (i32.const 1))) (br $next)))
    (struct.set {D} $used (local.get $d) (select (local.get $j) (local.get $n) (local.get $compact))))
  (func {D}.set (param $d (ref {D})) (param $k {K}) (param $v {V})
    (local $p i32) (local $n i32) (local $used i32)
    (local.set $p (call {D}.find (local.get $d) (local.get $k)))
    (local.set $n (array.get $u32s (struct.get {D} $slots (local.get $d)) (local.get $p)))
    (if (i32.gt_s (local.get $n) (i32.const 0)) (then
      (struct.set {E} $value (ref.as_non_null (array.get {A} (struct.get {D} $entries (local.get $d))
        (i32.sub (local.get $n) (i32.const 1)))) (local.get $v)) (return)))
    (local.set $used (struct.get {D} $used (local.get $d)))
    (if (i32.eq (local.get $used) (array.len (struct.get {D} $entries (local.get $d)))) (then
      (call {D}.grow (local.get $d))
      (local.set $used (struct.get {D} $used (local.get $d)))
      (local.set $p (call {D}.find (local.get $d) (local.get $k)))))
    (array.set {A} (struct.get {D} $entries (local.get $d)) (local.get $used)
      (struct.new {E} (local.get $k) (local.get $v)))
    (array.set $u32s (struct.get {D} $slots (local.get $d)) (local.get $p) (i32.add (local.get $used) (i32.const 1)))
    (struct.set {D} $used (local.get $d) (i32.add (local.get $used) (i32.const 1)))
    (struct.set {D} $len (local.get $d) (i32.add (struct.get {D} $len (local.get $d)) (i32.const 1))))"""

    def dict_helper(self, t: DictT, op: str) -> str:
        D = self.heap(t)
        E, A = D + ".entry", D + ".entries"
        K, V = self.valtype(t.key), self.valtype(t.value)
        d = "(local.get $d)"
        entries = f"(struct.get {D} $entries {d})"
        length = f"(struct.get {D} $len {d})"
        used = f"(struct.get {D} $used {d})"
        head = f"  (func {D}.{op} (param $d (ref {D}))"
        entry = f"(ref.as_non_null (array.get {A} {entries} (local.get $i)))"
        key = f"(struct.get {E} $key {entry})"
        value = f"(struct.get {E} $value {entry})"
        if op == "core":
            return self.dict_core(t)
        if op == "len":
            return head + f" (result i32) {length})"
        if op == "iter_start":
            return head + f" (result i32) (struct.set {D} $iters {d} " \
                          f"(i32.add (struct.get {D} $iters {d}) (i32.const 1))) {length})"
        if op == "iter_end":
            return head + f" (struct.set {D} $iters {d} (i32.sub (struct.get {D} $iters {d}) (i32.const 1))))"
        if op in ("keys", "values", "items"):
            T = self.heap(DictViewT(t, op))
            return head + f" (result (ref {T})) (struct.new {T} {d}))"
        if op.endswith("_at"):
            kind = op.removesuffix("_at")
            elem = DictViewT(t, kind).elem
            result = key if kind == "keys" else value if kind == "values" else f"(struct.new {self.heap(elem)} {key} {value})"
            return head + f" (param $i i32) (result {self.valtype(elem)}) {result})"
        if op == "next":
            return head + f""" (param $i i32) (param $size i32) (param $remaining i32) (result i32)
    (if (i32.ne {length} (local.get $size)) (then {self.raise_code('RuntimeError', 'dictionary changed size during iteration')}))
    (block $done (loop $scan
      (br_if $done (i32.ge_u (local.get $i) {used}))
      (if (i32.eqz (ref.is_null (array.get {A} {entries} (local.get $i)))) (then
        (if (i32.eqz (local.get $remaining)) (then {self.raise_code('RuntimeError', 'dictionary keys changed during iteration')}))
        (return (local.get $i))))
      (local.set $i (i32.add (local.get $i) (i32.const 1))) (br $scan)))
    (i32.const -1))"""
        if op in ("contains", "get", "pop", "delete", "get_default", "get_optional", "pop_default", "pop_optional", "setdefault_default"):
            default = "_" in op
            ret = OptionalT(t.value) if op.endswith("optional") and not isinstance(t.value, OptionalT) else t.value
            R = self.valtype(ret)
            params = f" (param $fallback {R})" if default else ""
            result = "" if op == "delete" else " (result i32)" if op == "contains" else f" (result {R})"
            missing = (f"(return (local.get $fallback))" if default else
                       "(return (i32.const 0))" if op == "contains" else self.key_error(t.key, "(local.get $k)"))
            if op == "setdefault_default":
                missing = f"(call {D}.set {d} (local.get $k) (local.get $fallback)) " + missing
            body = [f"(local $p i32) (local $n i32) (local $e (ref {E}))",
                    f"(local.set $p (call {D}.find {d} (local.get $k)))",
                    f"(local.set $n (array.get $u32s (struct.get {D} $slots {d}) (local.get $p)))",
                    f"(if (i32.le_s (local.get $n) (i32.const 0)) (then {missing}))",
                    f"(local.set $e (ref.as_non_null (array.get {A} {entries} (i32.sub (local.get $n) (i32.const 1)))))"]
            if op == "delete" or op.startswith("pop"):
                body += [f"(array.set $u32s (struct.get {D} $slots {d}) (local.get $p) (i32.const -1))",
                         f"(array.set {A} {entries} (i32.sub (local.get $n) (i32.const 1)) (ref.null {E}))",
                         f"(struct.set {D} $len {d} (i32.sub {length} (i32.const 1)))"]
            if op != "delete":
                body.append("(i32.const 1)" if op == "contains" else f"(struct.get {E} $value (local.get $e))")
            return head + f" (param $k {K}){params}{result}\n    " + "\n    ".join(body) + ")"
        if op == "clear":
            return head + f"""
    (struct.set {D} $len {d} (i32.const 0)) (struct.set {D} $used {d} (i32.const 0))
    (struct.set {D} $slots {d} (array.new_default $u32s (i32.const 8)))
    (struct.set {D} $entries {d} (array.new_default {A} (i32.const 4))))"""
        if op in ("union", "ior"):
            update = self.collection_op(t, "update")
            value = f"(call {self.collection_op(t, 'copy')} {d})" if op == "union" else d
            return head + f""" (param $other (ref {D})) (result (ref {D})) (local $out (ref {D}))
    (local.set $out {value}) (call {update} (local.get $out) (local.get $other)) (local.get $out))"""
        if op == "copy":
            update = self.collection_op(t, "update")
            return head + f""" (result (ref {D})) (local $out (ref {D}))
    (local.set $out (call {D}.new)) (call {update} (local.get $out) {d}) (local.get $out))"""
        if op == "update":
            return head + f""" (param $other (ref {D}))
    (local $i i32) (local $e (ref null {E}))
    (block $done (loop $scan
      (br_if $done (i32.ge_u (local.get $i) (struct.get {D} $used (local.get $other))))
      (local.set $e (array.get {A} (struct.get {D} $entries (local.get $other)) (local.get $i)))
      (if (i32.eqz (ref.is_null (local.get $e))) (then
        (call {D}.set {d} (struct.get {E} $key (ref.as_non_null (local.get $e)))
          (struct.get {E} $value (ref.as_non_null (local.get $e))))))
      (local.set $i (i32.add (local.get $i) (i32.const 1))) (br $scan))))"""
        if op == "popitem":
            pair = TupleT((t.key, t.value))
            P = self.heap(pair)
            delete = self.collection_op(t, "delete")
            return head + f""" (result (ref {P})) (local $i i32) (local $out (ref {P}))
    (if (i32.eqz {length}) (then {self.raise_code('KeyError', 'popitem(): dictionary is empty')}))
    (local.set $i {used})
    (loop $scan
      (local.set $i (i32.sub (local.get $i) (i32.const 1)))
      (br_if $scan (ref.is_null (array.get {A} {entries} (local.get $i)))))
    (local.set $out (struct.new {P} {key} {value}))
    (call {delete} {d} {key})
    (local.get $out))"""
        if op == "eq":
            contains, get = self.collection_op(t, "contains"), self.collection_op(t, "get")
            comparison = self.eq_code(t.value, value, f"(call {get} (local.get $other) {key})")
            return f"""  (func {D}.eq (param $a (ref null {D})) (param $b (ref null {D})) (result i32)
    (local $d (ref {D})) (local $other (ref {D})) (local $i i32)
    (if (ref.eq (local.get $a) (local.get $b)) (then (return (i32.const 1))))
    (if (i32.or (ref.is_null (local.get $a)) (ref.is_null (local.get $b))) (then (return (i32.const 0))))
    (local.set $d (ref.as_non_null (local.get $a)))
    (local.set $other (ref.as_non_null (local.get $b)))
    (if (i32.ne {length} (struct.get {D} $len (local.get $other))) (then (return (i32.const 0))))
    (block $done (loop $scan
      (br_if $done (i32.ge_u (local.get $i) {used}))
      (if (i32.eqz (ref.is_null (array.get {A} {entries} (local.get $i)))) (then
        (if (i32.eqz (call {contains} (local.get $other) {key})) (then (return (i32.const 0))))
        (if (i32.eqz {comparison}) (then (return (i32.const 0))))))
      (local.set $i (i32.add (local.get $i) (i32.const 1))) (br $scan)))
    (i32.const 1))"""
        if op == "list":
            view = DictViewT(t, "keys")
            fn = self.collection_op(view, "list")
            T = self.heap(view)
            return head + f" (result {self.valtype(ListT(t.key))}) (call {fn} (struct.new {T} {d})))"
        if op == "to_str":
            k, v = self.repr_code(t.key, key), self.repr_code(t.value, value)
            return head + f""" (result (ref $str)) (local $buf (ref $buf)) (local $i i32) (local $n i32)
    (local.set $buf (call $rt.buf_new (i32.const 32)))
    (call $rt.buf_str (local.get $buf) {self.lit('{')})
    (block $done (loop $scan
      (br_if $done (i32.ge_u (local.get $i) {used}))
      (if (i32.eqz (ref.is_null (array.get {A} {entries} (local.get $i)))) (then
        (if (local.get $n) (then (call $rt.buf_str (local.get $buf) {self.lit(', ')})))
        (call $rt.buf_str (local.get $buf) {k})
        (call $rt.buf_str (local.get $buf) {self.lit(': ')})
        (call $rt.buf_str (local.get $buf) {v})
        (local.set $n (i32.add (local.get $n) (i32.const 1)))))
      (local.set $i (i32.add (local.get $i) (i32.const 1))) (br $scan)))
    (call $rt.buf_str (local.get $buf) {self.lit('}')}) (call $rt.buf_done (local.get $buf)))"""
        raise AssertionError(f"dict helper {op}")

    def key_error(self, t: Type, value: str) -> str:
        # A preformatted key repr also handles integer and tuple keys without
        # boxing them, and prevents str(KeyError) from quoting the text twice.
        return ("(throw $snek.exc (struct.new $KeyError (global.get $KeyError.vt.inst) "
                f"(ref.null $str) {self.repr_code(t, value)}))")

    def view_helper(self, t: DictViewT, op: str) -> str:
        T, D = self.heap(t), self.heap(t.mapping)
        head = f"  (func {T}.{op} (param $v (ref {T}))"
        d = f"(struct.get {T} $dict (local.get $v))"
        if op == "mapping":
            return head + f" (result (ref {D})) {d})"
        if op == "len":
            return head + f" (result i32) (struct.get {D} $len {d}))"
        if op == "contains" and t.kind == "keys":
            fn = self.collection_op(t.mapping, "contains")
            return head + f" (param $x {self.valtype(t.elem)}) (result i32) (call {fn} {d} (local.get $x)))"
        if op == "to_str":
            lst = self.collection_op(t, "list")
            text = self.to_str_code(ListT(t.elem), f"(call {lst} (local.get $v))")
            return (head + " (result (ref $str)) (call $rt.str_concat_n (array.new_fixed $strs 3 "
                    f"{self.lit('dict_' + t.kind + '(')} {text} {self.lit(')')})))")
        if op in ("list", "contains"):
            elem = self.valtype(t.elem)
            at = self.collection_op(t.mapping, t.kind + "_at")
            value = f"(call {at} {d} (local.get $i))"
            if op == "list":
                L = self.heap(ListT(t.elem))
                append = L + ".append"
                # Lists use $list.<element> and the corresponding $arr.<element>.
                array = "$arr." + L.removeprefix("$list.")
                signature = f" (result (ref {L})) (local $out (ref {L})) (local $i i32)"
                init = f"(local.set $out (struct.new {L} (i32.const 0) (array.new_default {array} (struct.get {D} $len {d}))))"
                action = f"(call {append} (local.get $out) {value})"
                result = "(local.get $out)"
            else:
                signature = f" (param $x {elem}) (result i32) (local $i i32)"
                init = ""
                action = f"(if {self.eq_code(t.elem, value, '(local.get $x)')} (then (return (i32.const 1))))"
                result = "(i32.const 0)"
            return head + signature + f"""
    {init}
    (block $done (loop $scan
      (br_if $done (i32.ge_u (local.get $i) (struct.get {D} $used {d})))
      (if (i32.eqz (ref.is_null (array.get {D}.entries (struct.get {D} $entries {d}) (local.get $i)))) (then {action}))
      (local.set $i (i32.add (local.get $i) (i32.const 1))) (br $scan)))
    {result})"""
        raise AssertionError(f"dict view helper {op}")
