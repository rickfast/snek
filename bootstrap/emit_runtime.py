"""Collection and byte-stream WAT generation in Snek.

Initially ported mechanically from the reference generators, then maintained
as typed Snek source. No source transformation is part of compiler execution.
"""

from __future__ import annotations

from snek import i32

from bootstrap.declarations import DeclType

from bootstrap.bodies import inner_type

from bootstrap.emit_program import SourceEmitter


def emit_element(ty: DeclType) -> DeclType:
    if ty.kind == "view":
        mapping = ty.args[0]
        if ty.name == "items": return DeclType("tuple").add(mapping.args[0]).add(mapping.args[1])
        return mapping.args[1] if ty.name == "values" else mapping.args[0]
    return ty.args[0]


def emit_tuple_helper(emitter: SourceEmitter, ty: DeclType, op: str) -> str:
    T = emitter.types.heap(ty)
    head = "  (func " + T + "." + op
    def field(i: i32, obj: str = "$v") -> str:
        return f"(struct.get {T} {i} (local.get {obj}))"
    if op == "eq":
        tests: list[str] = []
        for i in range(len(ty.args)):
            tests.append("(if (i32.eqz " + emitter.equal(ty.args[i], field(i, "$x"), field(i, "$y")) + ") (then (return (i32.const 0))))")
        return head + f" (param $a (ref null {T})) (param $b (ref null {T})) (result i32) (local $x (ref {T})) (local $y (ref {T})) (if (ref.eq (local.get $a) (local.get $b)) (then (return (i32.const 1)))) (if (i32.or (ref.is_null (local.get $a)) (ref.is_null (local.get $b))) (then (return (i32.const 0)))) (local.set $x (ref.as_non_null (local.get $a))) (local.set $y (ref.as_non_null (local.get $b))) {' '.join(tests)} (i32.const 1))"
    if op == "hash":
        mixes: list[str] = []
        for i in range(len(ty.args)):
            mixes.append("(local.set $h (i32.mul (i32.xor (local.get $h) " + emit_hash(emitter, ty.args[i], field(i, "$v")) + ") (i32.const 16777619)))")
        return head + f" (param $v (ref {T})) (result i32) (local $h i32) (local.set $h (i32.const -2128831035)) {' '.join(mixes)} (local.get $h))"
    if op == "to_str":
        parts = [emitter.literals.lit("(")]
        for i in range(len(ty.args)):
            if i > 0: parts.append(emitter.literals.lit(", "))
            parts.append(emitter.to_string(ty.args[i], field(i, "$v"), True))
        parts.append(emitter.literals.lit(",)" if len(ty.args) == 1 else ")"))
        return head + f" (param $v (ref {T})) (result (ref $str)) (call $rt.str_concat_n (array.new_fixed $strs {len(parts)} {' '.join(parts)})))"
    elem = ty.args[0]
    if op == "get":
        choices: list[str] = []
        for i in range(len(ty.args)): choices.append(f"(if (i32.eq (local.get $i) (i32.const {i})) (then (return {field(i, "$v")})))")
        return head + f" (param $v (ref {T})) (param $i i32) (result {emitter.types.valtype(elem)}) (if (i32.lt_s (local.get $i) (i32.const 0)) (then (local.set $i (i32.add (local.get $i) (i32.const {len(ty.args)}))))) {' '.join(choices)} {emitter.error('IndexError', 'tuple index out of range')})"
    if op in ("contains", "count", "index"):
        params = " (param $start i32) (param $stop i32)" if op == "index" else ""
        body: list[str] = ["(local $n i32)"] if op == "count" else []
        if op == "index":
            for p in ("start", "stop"):
                body.append(f"(if (i32.lt_s (local.get ${p}) (i32.const 0)) (then (local.set ${p} (i32.add (local.get ${p}) (i32.const {len(ty.args)})))))")
        for i in range(len(ty.args)):
            eq = emitter.equal(elem, field(i, "$v"), "(local.get $x)")
            if op == "index": eq = f"(if (result i32) (i32.and (i32.ge_s (i32.const {i}) (local.get $start)) (i32.lt_s (i32.const {i}) (local.get $stop))) (then {eq}) (else (i32.const 0)))"
            action = "(local.set $n (i32.add (local.get $n) (i32.const 1)))" if op == "count" else f"(return (i32.const {i if op == 'index' else 1}))"
            body.append(f"(if {eq} (then {action}))")
        body.append("(local.get $n)" if op == "count" else "(i32.const 0)" if op == "contains" else emitter.error("ValueError", "tuple.index(x): x not in tuple"))
        return head + f" (param $v (ref {T})) (param $x {emitter.types.valtype(elem)}){params} (result i32) " + " ".join(body) + ")"
    raise ValueError("unknown tuple helper " + op)


def emit_collection_helpers(emitter: SourceEmitter) -> str:
    out: list[str] = []
    done: dict[str, bool] = {}
    while True:
        emitter.types.close()
        tasks: list[tuple[str, DeclType, str]] = []
        for name, ty in list(emitter.types.lists.items()):
            tasks.append(("$list." + name + ":basic", ty, "basic"))
            for op in sorted(emitter.ops.get("$list." + name, [])):
                tasks.append(("$list." + name + ":" + op, ty, op))
        for registry in (emitter.types.tuples, emitter.types.dicts, emitter.types.views):
            for name, ty in list(registry.items()):
                for op in sorted(emitter.ops.get("$" + name, [])): tasks.append(("$" + name + ":" + op, ty, op))
        for name, ty in list(emitter.optional_reprs.items()):
            tasks.append(("optional:" + name, DeclType("optional").add(ty), "repr"))
        pending = False
        for key, ty, op in tasks:
            if key in done: continue
            pending = True
            done[key] = True
            if ty.kind == "list":
                name = emitter.types.heap(ty)[6:]
                out.append(emit_list_basic(emitter, name, ty) if op == "basic" else emit_list_operation(emitter, name, ty, op))
            elif ty.kind == "tuple": out.append(emit_tuple_helper(emitter, ty, op))
            elif ty.kind == "dict": out.append(emit_dict_helper(emitter, ty, op))
            elif ty.kind == "view": out.append(emit_view_helper(emitter, ty, op))
            elif ty.kind == "optional":
                inner = ty.args[0]
                name = emitter.types.heap(inner)[1:]
                out.append(f"  (func $optional.{name}.repr (param $v (ref null ${name})) (result (ref $str)) (if (result (ref $str)) (ref.is_null (local.get $v)) (then {emitter.literals.lit('None')}) (else {emitter.to_string(inner, '(ref.as_non_null (local.get $v))', True)})))")
        if not pending: return "\n".join(out)

def emit_list_operation(self: SourceEmitter, m: str, t: DeclType, op: str) -> str:
    if op == 'sorted' or (op.startswith('sum_') or op.startswith('min') or op.startswith('max')):
        return emit_list_builtin(self, t, op)
    e = emit_element(t)
    A, L = (f'$arr.{m}', f'$list.{m}')
    R, S = (self.types.valtype(e), self.types.storage(e))
    get = 'array.get_u' if e.same(DeclType('bool')) else 'array.get'
    nn = e.reference() and (not e.kind in ('optional',))
    LEN, DATA = (f'(struct.get {L} $len (local.get $l))', f'(struct.get {L} $data (local.get $l))')

    def load(arr: str, idx: str) -> str:
        code = f'({get} {A} (local.get {arr}) (local.get {idx}))'
        return f'(ref.as_non_null {code})' if nn else code
    clear = f'(array.set {A} (local.get $d) (local.get $n) (ref.null {self.types.heap(e)}))' if e.reference() else ''
    shift_down = f'(array.copy {A} {A} (local.get $d) (local.get $i) (local.get $d) (i32.add (local.get $i) (i32.const 1)) (i32.sub (local.get $n) (local.get $i)))'

    def loop(var: str, end: str, body: str) -> str:
        return f'(block $done (loop $next (br_if $done (i32.ge_s (local.get {var}) {end})) {body} (local.set {var} (i32.add (local.get {var}) (i32.const 1))) (br $next)))'

    def clamp(v: str) -> str:
        return f'(if (i32.lt_s (local.get {v}) (i32.const 0)) (then (local.set {v} (i32.add (local.get {v}) (local.get $n))) (if (i32.lt_s (local.get {v}) (i32.const 0)) (then (local.set {v} (i32.const 0))))))'
    head = f'  (func {L}.{op} '
    if op == 'slice':
        body = f"(param $l (ref {L})) (param $start i32) (param $stop i32) (param $step i32) (param $flags i32) (result (ref {L}))\n    (local $cnt i32) (local $o (ref {A})) (local $d (ref {A})) (local $k i32) (local $p i32)\n    (call $rt.step_check (local.get $step))\n    (local.set $cnt (call $rt.slice_adj {LEN} (local.get $start) (local.get $stop) (local.get $step) (local.get $flags)))\n    (local.set $p (global.get $rt.slice_start))\n    (local.set $d {DATA})\n    (local.set $o (array.new_default {A} (local.get $cnt)))\n    (if (i32.eq (local.get $step) (i32.const 1))\n      (then (array.copy {A} {A} (local.get $o) (i32.const 0) (local.get $d) (local.get $p) (local.get $cnt)))\n      (else {loop('$k', '(local.get $cnt)', f'(array.set {A} (local.get $o) (local.get $k) ({get} {A} (local.get $d) (local.get $p))) (local.set $p (i32.add (local.get $p) (local.get $step)))')}))\n    (struct.new {L} (local.get $cnt) (local.get $o)))"
    elif op == 'copy':
        body = f'(param $l (ref {L})) (result (ref {L}))\n    (call {L}.slice (local.get $l) (i32.const 0) (i32.const 0) (i32.const 1) (i32.const 0)))'
        self.operation(t, 'slice')
    elif op == 'find':
        body = f"""(param $l (ref {L})) (param $v {R}) (param $i i32) (param $stop i32) (result i32)\n    (local $d (ref {A}))\n    {loop('$i', '(local.get $stop)', f"(br_if $done (i32.ge_u (local.get $i) {LEN})) (local.set $d {DATA}) (if {self.equal(e, load('$d', '$i'), '(local.get $v)')} (then (return (local.get $i))))")}\n    (i32.const -1))"""
    elif op == 'contains':
        self.operation(t, 'find')
        body = f'(param $l (ref {L})) (param $v {R}) (result i32)\n    (i32.ge_s (call {L}.find (local.get $l) (local.get $v) (i32.const 0) (i32.const 0x7FFFFFFF)) (i32.const 0)))'
    elif op == 'index':
        self.operation(t, 'find')
        msg = f"(call $rt.str_concat {self.to_string(e, '(local.get $v)', True)} {self.literals.lit(' is not in list')})"
        body = f"(param $l (ref {L})) (param $v {R}) (param $start i32) (param $stop i32) (result i32)\n    (local $n i32) (local $r i32)\n    (local.set $n {LEN})\n    {clamp('$start')}\n    {clamp('$stop')}\n    (local.set $r (call {L}.find (local.get $l) (local.get $v) (local.get $start) (local.get $stop)))\n    (if (i32.lt_s (local.get $r) (i32.const 0)) (then (throw $snek.exc (call $ValueError.__new__ {msg}))))\n    (local.get $r))"
    elif op == 'count':
        body = f"""(param $l (ref {L})) (param $v {R}) (result i32)\n    (local $d (ref {A})) (local $i i32) (local $c i32)\n    {loop('$i', LEN, f"(local.set $d {DATA}) (if {self.equal(e, load('$d', '$i'), '(local.get $v)')} (then (local.set $c (i32.add (local.get $c) (i32.const 1)))))")}\n    (local.get $c))"""
    elif op == 'insert':
        body = f"(param $l (ref {L})) (param $i i32) (param $v {R})\n    (local $n i32) (local $d (ref {A}))\n    (local.set $n {LEN})\n    {clamp('$i')}\n    (if (i32.gt_s (local.get $i) (local.get $n)) (then (local.set $i (local.get $n))))\n    (call {L}.append (local.get $l) (local.get $v))\n    (local.set $d {DATA})\n    (array.copy {A} {A} (local.get $d) (i32.add (local.get $i) (i32.const 1)) (local.get $d) (local.get $i)\n                (i32.sub (local.get $n) (local.get $i)))\n    (array.set {A} (local.get $d) (local.get $i) (local.get $v)))"
    elif op == 'extend':
        body = f'''(param $l (ref {L})) (param $o (ref {L}))
    (local $n i32) (local $k i32) (local $need i32) (local $cap i32) (local $d (ref {A})) (local $g (ref null {A}))
    (local.set $n {LEN})
    (local.set $k (struct.get {L} $len (local.get $o)))  ;; before growing: o may be l itself
    (local.set $need (i32.add (local.get $n) (local.get $k)))
    (local.set $d {DATA})
    (if (i32.gt_u (local.get $need) (array.len (local.get $d)))
      (then
        (local.set $cap (i32.shl (array.len (local.get $d)) (i32.const 1)))
        (if (i32.lt_u (local.get $cap) (local.get $need)) (then (local.set $cap (local.get $need))))
        (local.set $g (array.new_default {A} (local.get $cap)))
        (array.copy {A} {A} (local.get $g) (i32.const 0) (local.get $d) (i32.const 0) (local.get $n))
        (local.set $d (ref.as_non_null (local.get $g)))
        (struct.set {L} $data (local.get $l) (local.get $d))))
    (array.copy {A} {A} (local.get $d) (local.get $n) (struct.get {L} $data (local.get $o)) (i32.const 0) (local.get $k))
    (struct.set {L} $len (local.get $l) (local.get $need)))'''
    elif op == 'remove':
        self.operation(t, 'find')
        body = f"(param $l (ref {L})) (param $v {R})\n    (local $i i32) (local $n i32) (local $d (ref {A}))\n    (local.set $i (call {L}.find (local.get $l) (local.get $v) (i32.const 0) (i32.const 0x7FFFFFFF)))\n    (if (i32.lt_s (local.get $i) (i32.const 0))\n      (then {self.error('ValueError', 'list.remove(x): x not in list')}))\n    (if (i32.ge_u (local.get $i) {LEN}) (then (return)))  ;; __eq__ may have shrunk the list\n    (local.set $n (i32.sub {LEN} (i32.const 1)))\n    (local.set $d {DATA})\n    {shift_down}\n    {clear}\n    (struct.set {L} $len (local.get $l) (local.get $n)))"
    elif op == 'pop_at':
        val = '(ref.as_non_null (local.get $v))' if nn else '(local.get $v)'
        body = f"(param $l (ref {L})) (param $i i32) (result {R})\n    (local $n i32) (local $d (ref {A})) (local $v {S})\n    (local.set $n {LEN})\n    (if (i32.eqz (local.get $n)) (then {self.error('IndexError', 'pop from empty list')}))\n    (if (i32.lt_s (local.get $i) (i32.const 0)) (then (local.set $i (i32.add (local.get $i) (local.get $n)))))\n    (if (i32.ge_u (local.get $i) (local.get $n)) (then {self.error('IndexError', 'pop index out of range')}))\n    (local.set $d {DATA})\n    (local.set $v ({get} {A} (local.get $d) (local.get $i)))\n    (local.set $n (i32.sub (local.get $n) (i32.const 1)))\n    {shift_down}\n    {clear}\n    (struct.set {L} $len (local.get $l) (local.get $n))\n    {val})"
    elif op == 'reverse':
        body = f'(param $l (ref {L}))\n    (local $d (ref {A})) (local $i i32) (local $j i32) (local $t {S})\n    (local.set $d {DATA})\n    (local.set $j (i32.sub {LEN} (i32.const 1)))\n    (block $done\n      (loop $next\n        (br_if $done (i32.ge_s (local.get $i) (local.get $j)))\n        (local.set $t ({get} {A} (local.get $d) (local.get $i)))\n        (array.set {A} (local.get $d) (local.get $i) ({get} {A} (local.get $d) (local.get $j)))\n        (array.set {A} (local.get $d) (local.get $j) (local.get $t))\n        (local.set $i (i32.add (local.get $i) (i32.const 1)))\n        (local.set $j (i32.sub (local.get $j) (i32.const 1)))\n        (br $next))))'
    elif op == 'clear':
        body = f'(param $l (ref {L}))\n    (struct.set {L} $len (local.get $l) (i32.const 0))\n    (struct.set {L} $data (local.get $l) (array.new_default {A} (i32.const 0))))'
    elif op == 'concat':
        body = f'(param $a (ref {L})) (param $b (ref {L})) (result (ref {L}))\n    (local $na i32) (local $nb i32) (local $o (ref {A}))\n    (local.set $na (struct.get {L} $len (local.get $a)))\n    (local.set $nb (struct.get {L} $len (local.get $b)))\n    (local.set $o (array.new_default {A} (i32.add (local.get $na) (local.get $nb))))\n    (array.copy {A} {A} (local.get $o) (i32.const 0) (struct.get {L} $data (local.get $a)) (i32.const 0) (local.get $na))\n    (array.copy {A} {A} (local.get $o) (local.get $na) (struct.get {L} $data (local.get $b)) (i32.const 0) (local.get $nb))\n    (struct.new {L} (i32.add (local.get $na) (local.get $nb)) (local.get $o)))'
    elif op == 'repeat':
        body = f"(param $l (ref {L})) (param $k i32) (result (ref {L}))\n    (local $n i32) (local $o (ref {A})) (local $d (ref {A})) (local $i i32) (local $total i64)\n    (local.set $n {LEN})\n    (if (i32.lt_s (local.get $k) (i32.const 0)) (then (local.set $k (i32.const 0))))\n    (local.set $total (i64.mul (i64.extend_i32_u (local.get $n)) (i64.extend_i32_u (local.get $k))))\n    (if (i64.gt_u (local.get $total) (i64.const 0x7FFFFFFF))\n      (then (throw $snek.exc (call $MemoryError.__new__ (ref.null $str)))))\n    (local.set $o (array.new_default {A} (i32.wrap_i64 (local.get $total))))\n    (local.set $d {DATA})\n    {loop('$i', '(local.get $k)', f'(array.copy {A} {A} (local.get $o) (i32.mul (local.get $i) (local.get $n)) (local.get $d) (i32.const 0) (local.get $n))')}\n    (struct.new {L} (i32.wrap_i64 (local.get $total)) (local.get $o)))"
    elif op == 'imul':
        self.operation(t, 'repeat')
        body = f'(param $l (ref {L})) (param $k i32)\n    (local $r (ref {L}))\n    (local.set $r (call {L}.repeat (local.get $l) (local.get $k)))\n    (struct.set {L} $data (local.get $l) (struct.get {L} $data (local.get $r)))\n    (struct.set {L} $len (local.get $l) (struct.get {L} $len (local.get $r))))'
    elif op == 'eq':
        body = f"(param $a (ref null {L})) (param $b (ref null {L})) (result i32)\n    (local $x (ref {L})) (local $y (ref {L})) (local $n i32) (local $i i32) (local $da (ref {A})) (local $db (ref {A}))\n    (if (ref.eq (local.get $a) (local.get $b)) (then (return (i32.const 1))))\n    (if (i32.or (ref.is_null (local.get $a)) (ref.is_null (local.get $b))) (then (return (i32.const 0))))\n    (local.set $x (ref.as_non_null (local.get $a)))\n    (local.set $y (ref.as_non_null (local.get $b)))\n    (local.set $n (struct.get {L} $len (local.get $x)))\n    (if (i32.ne (local.get $n) (struct.get {L} $len (local.get $y))) (then (return (i32.const 0))))\n    (block $done (loop $next\n      (br_if $done (i32.ge_u (local.get $i) (struct.get {L} $len (local.get $x))))\n      (br_if $done (i32.ge_u (local.get $i) (struct.get {L} $len (local.get $y))))\n      (local.set $da (struct.get {L} $data (local.get $x)))\n      (local.set $db (struct.get {L} $data (local.get $y)))\n      (br_if $done (i32.eqz {self.equal(e, load('$da', '$i'), load('$db', '$i'))}))\n      (local.set $i (i32.add (local.get $i) (i32.const 1))) (br $next)))\n    (if (result i32) (i32.or\n          (i32.ge_u (local.get $i) (struct.get {L} $len (local.get $x)))\n          (i32.ge_u (local.get $i) (struct.get {L} $len (local.get $y))))\n      (then (i32.eq (struct.get {L} $len (local.get $x)) (struct.get {L} $len (local.get $y))))\n      (else (i32.const 0))))"
    else:
        raise ValueError(f'list op {op}')
    return head + body

def emit_list_basic(self: SourceEmitter, m: str, t: DeclType) -> str:
    e = emit_element(t)
    R = self.types.valtype(e)
    get = 'array.get_u' if e.same(DeclType('bool')) else 'array.get'
    nn = e.reference() and (not e.kind in ('optional',))
    load = f'({get} $arr.{m} (struct.get $list.{m} $data (local.get $l)) (local.get $i))'
    if nn:
        load = f'(ref.as_non_null {load})'
    clear = f'\n    (array.set $arr.{m} (local.get $d) (local.get $n) (ref.null {self.types.heap(e)}))  ;; drop the reference so the GC can reclaim it' if e.reference() else ''
    pop_val = '(ref.as_non_null (local.get $v))' if nn else '(local.get $v)'
    oob = self.error('IndexError', 'list index out of range')
    oob_set = self.error('IndexError', 'list assignment index out of range')
    empty = self.error('IndexError', 'pop from empty list')
    code = f'''  (func $list.{m}.get (param $l (ref $list.{m})) (param $i i32) (result {R})
    (local $n i32)
    (local.set $n (struct.get $list.{m} $len (local.get $l)))
    (if (i32.lt_s (local.get $i) (i32.const 0)) (then (local.set $i (i32.add (local.get $i) (local.get $n)))))
    (if (i32.ge_u (local.get $i) (local.get $n)) (then {oob}))
    {load})
  (func $list.{m}.set (param $l (ref $list.{m})) (param $i i32) (param $v {R})
    (local $n i32)
    (local.set $n (struct.get $list.{m} $len (local.get $l)))
    (if (i32.lt_s (local.get $i) (i32.const 0)) (then (local.set $i (i32.add (local.get $i) (local.get $n)))))
    (if (i32.ge_u (local.get $i) (local.get $n)) (then {oob_set}))
    (array.set $arr.{m} (struct.get $list.{m} $data (local.get $l)) (local.get $i) (local.get $v)))
  (func $list.{m}.append (param $l (ref $list.{m})) (param $v {R})
    (local $n i32) (local $d (ref $arr.{m})) (local $grown (ref null $arr.{m}))
    (local.set $n (struct.get $list.{m} $len (local.get $l)))
    (local.set $d (struct.get $list.{m} $data (local.get $l)))
    (if (i32.eq (local.get $n) (array.len (local.get $d)))
      (then
        (local.set $grown (array.new_default $arr.{m}
          (select (i32.const 4) (i32.shl (local.get $n) (i32.const 1)) (i32.lt_u (local.get $n) (i32.const 2)))))
        (array.copy $arr.{m} $arr.{m} (local.get $grown) (i32.const 0) (local.get $d) (i32.const 0) (local.get $n))
        (local.set $d (ref.as_non_null (local.get $grown)))
        (struct.set $list.{m} $data (local.get $l) (local.get $d))))
    (array.set $arr.{m} (local.get $d) (local.get $n) (local.get $v))
    (struct.set $list.{m} $len (local.get $l) (i32.add (local.get $n) (i32.const 1))))
  (func $list.{m}.pop (param $l (ref $list.{m})) (result {R})
    (local $n i32) (local $d (ref $arr.{m})) (local $v {self.types.storage(e)})
    (local.set $n (struct.get $list.{m} $len (local.get $l)))
    (if (i32.eqz (local.get $n)) (then {empty}))
    (local.set $n (i32.sub (local.get $n) (i32.const 1)))
    (local.set $d (struct.get $list.{m} $data (local.get $l)))
    (local.set $v ({get} $arr.{m} (local.get $d) (local.get $n))){clear}
    (struct.set $list.{m} $len (local.get $l) (local.get $n))
    {pop_val})'''
    if self.checked.has_repr(e):
        item = self.to_string(e, f'(call $list.{m}.get (local.get $l) (local.get $i))', True)
        code += f"\n  (func $list.{m}.to_str (param $l (ref $list.{m})) (result (ref $str))\n    (local $s (ref null $str)) (local $i i32) (local $n i32)\n    (local.set $s {self.literals.lit('[')})\n    (local.set $n (struct.get $list.{m} $len (local.get $l)))\n    (block $done\n      (loop $next\n        (br_if $done (i32.ge_u (local.get $i) (local.get $n)))\n        (if (local.get $i)\n          (then (local.set $s (call $rt.str_concat (ref.as_non_null (local.get $s)) {self.literals.lit(', ')}))))\n        (local.set $s (call $rt.str_concat (ref.as_non_null (local.get $s)) {item}))\n        (local.set $i (i32.add (local.get $i) (i32.const 1)))\n        (br $next)))\n    (call $rt.str_concat (ref.as_non_null (local.get $s)) {self.literals.lit(']')}))"
    return code

def emit_hash(self: SourceEmitter, t: DeclType, value: str) -> str:
    if t.kind in ('optional',):
        return f"(if (result i32) (ref.is_null {value}) (then (i32.const 0)) (else {emit_hash(self, t.args[0], f'(ref.as_non_null {value})')}))"
    if t.same(DeclType('str')):
        return f'(call $rt.str_hash {value})'
    if t.kind in ('bool', 'i32'):
        return f'(i32.mul {value} (i32.const -1640531535))'
    if t.same(DeclType('i64')):
        return f'(i32.mul (i32.xor (i32.wrap_i64 {value}) (i32.wrap_i64 (i64.shr_u {value} (i64.const 32)))) (i32.const -1640531535))'
    if t.kind in ('tuple',):
        return f"(call {self.operation(t, 'hash')} {value})"
    raise ValueError('hash of ' + t.kind)

def emit_dict_core(self: SourceEmitter, t: DeclType) -> str:
    D = self.types.heap(t)
    E, A = (D + '.entry', D + '.entries')
    K, V = (self.types.valtype(t.args[0]), self.types.valtype(t.args[1]))
    key_hash = emit_hash(self, t.args[0], '(local.get $k)')
    same = self.equal(t.args[0], f'(struct.get {E} $key (local.get $e))', '(local.get $k)')
    return f'''  (func {D}.new (result (ref {D}))
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
    (struct.set {D} $len (local.get $d) (i32.add (struct.get {D} $len (local.get $d)) (i32.const 1))))'''

def emit_dict_helper(self: SourceEmitter, t: DeclType, op: str) -> str:
    D = self.types.heap(t)
    E, A = (D + '.entry', D + '.entries')
    K, V = (self.types.valtype(t.args[0]), self.types.valtype(t.args[1]))
    d = '(local.get $d)'
    entries = f'(struct.get {D} $entries {d})'
    length = f'(struct.get {D} $len {d})'
    used = f'(struct.get {D} $used {d})'
    head = f'  (func {D}.{op} (param $d (ref {D}))'
    entry = f'(ref.as_non_null (array.get {A} {entries} (local.get $i)))'
    key = f'(struct.get {E} $key {entry})'
    value = f'(struct.get {E} $value {entry})'
    if op == 'core':
        return emit_dict_core(self, t)
    if op == 'len':
        return head + f' (result i32) {length})'
    if op == 'iter_start':
        return head + f' (result i32) (struct.set {D} $iters {d} (i32.add (struct.get {D} $iters {d}) (i32.const 1))) {length})'
    if op == 'iter_end':
        return head + f' (struct.set {D} $iters {d} (i32.sub (struct.get {D} $iters {d}) (i32.const 1))))'
    if op in ('keys', 'values', 'items'):
        T = self.types.heap(DeclType('view', op).add(t))
        return head + f' (result (ref {T})) (struct.new {T} {d}))'
    if op.endswith('_at'):
        kind = op.removesuffix('_at')
        elem = emit_element(DeclType('view', kind).add(t))
        result = key if kind == 'keys' else value if kind == 'values' else f'(struct.new {self.types.heap(elem)} {key} {value})'
        return head + f' (param $i i32) (result {self.types.valtype(elem)}) {result})'
    if op == 'next':
        return head + f" (param $i i32) (param $size i32) (param $remaining i32) (result i32)\n    (if (i32.ne {length} (local.get $size)) (then {self.error('RuntimeError', 'dictionary changed size during iteration')}))\n    (block $done (loop $scan\n      (br_if $done (i32.ge_u (local.get $i) {used}))\n      (if (i32.eqz (ref.is_null (array.get {A} {entries} (local.get $i)))) (then\n        (if (i32.eqz (local.get $remaining)) (then {self.error('RuntimeError', 'dictionary keys changed during iteration')}))\n        (return (local.get $i))))\n      (local.set $i (i32.add (local.get $i) (i32.const 1))) (br $scan)))\n    (i32.const -1))"
    if op in ('contains', 'get', 'pop', 'delete', 'get_default', 'get_optional', 'pop_default', 'pop_optional', 'setdefault_default'):
        default = '_' in op
        ret = DeclType('optional').add(t.args[1]) if op.endswith('optional') and (not t.args[1].kind in ('optional',)) else t.args[1]
        R = self.types.valtype(ret)
        params = f' (param $fallback {R})' if default else ''
        result = '' if op == 'delete' else ' (result i32)' if op == 'contains' else f' (result {R})'
        missing = f'(return (local.get $fallback))' if default else '(return (i32.const 0))' if op == 'contains' else emit_key_error(self, t.args[0], '(local.get $k)')
        if op == 'setdefault_default':
            missing = f'(call {D}.set {d} (local.get $k) (local.get $fallback)) ' + missing
        body = [f'(local $p i32) (local $n i32) (local $e (ref {E}))', f'(local.set $p (call {D}.find {d} (local.get $k)))', f'(local.set $n (array.get $u32s (struct.get {D} $slots {d}) (local.get $p)))', f'(if (i32.le_s (local.get $n) (i32.const 0)) (then {missing}))', f'(local.set $e (ref.as_non_null (array.get {A} {entries} (i32.sub (local.get $n) (i32.const 1)))))']
        if op == 'delete' or op.startswith('pop'):
            body += [f'(array.set $u32s (struct.get {D} $slots {d}) (local.get $p) (i32.const -1))', f'(array.set {A} {entries} (i32.sub (local.get $n) (i32.const 1)) (ref.null {E}))', f'(struct.set {D} $len {d} (i32.sub {length} (i32.const 1)))']
        if op != 'delete':
            body.append('(i32.const 1)' if op == 'contains' else f'(struct.get {E} $value (local.get $e))')
        return head + f' (param $k {K}){params}{result}\n    ' + '\n    '.join(body) + ')'
    if op == 'clear':
        return head + f'\n    (struct.set {D} $len {d} (i32.const 0)) (struct.set {D} $used {d} (i32.const 0))\n    (struct.set {D} $slots {d} (array.new_default $u32s (i32.const 8)))\n    (struct.set {D} $entries {d} (array.new_default {A} (i32.const 4))))'
    if op in ('union', 'ior'):
        update = self.operation(t, 'update')
        value = f"(call {self.operation(t, 'copy')} {d})" if op == 'union' else d
        return head + f' (param $other (ref {D})) (result (ref {D})) (local $out (ref {D}))\n    (local.set $out {value}) (call {update} (local.get $out) (local.get $other)) (local.get $out))'
    if op == 'copy':
        update = self.operation(t, 'update')
        return head + f' (result (ref {D})) (local $out (ref {D}))\n    (local.set $out (call {D}.new)) (call {update} (local.get $out) {d}) (local.get $out))'
    if op == 'update':
        return head + f' (param $other (ref {D}))\n    (local $i i32) (local $e (ref null {E}))\n    (block $done (loop $scan\n      (br_if $done (i32.ge_u (local.get $i) (struct.get {D} $used (local.get $other))))\n      (local.set $e (array.get {A} (struct.get {D} $entries (local.get $other)) (local.get $i)))\n      (if (i32.eqz (ref.is_null (local.get $e))) (then\n        (call {D}.set {d} (struct.get {E} $key (ref.as_non_null (local.get $e)))\n          (struct.get {E} $value (ref.as_non_null (local.get $e))))))\n      (local.set $i (i32.add (local.get $i) (i32.const 1))) (br $scan))))'
    if op == 'popitem':
        pair = DeclType('tuple').add(t.args[0]).add(t.args[1])
        P = self.types.heap(pair)
        delete = self.operation(t, 'delete')
        return head + f" (result (ref {P})) (local $i i32) (local $out (ref {P}))\n    (if (i32.eqz {length}) (then {self.error('KeyError', 'popitem(): dictionary is empty')}))\n    (local.set $i {used})\n    (loop $scan\n      (local.set $i (i32.sub (local.get $i) (i32.const 1)))\n      (br_if $scan (ref.is_null (array.get {A} {entries} (local.get $i)))))\n    (local.set $out (struct.new {P} {key} {value}))\n    (call {delete} {d} {key})\n    (local.get $out))"
    if op == 'eq':
        contains, get = (self.operation(t, 'contains'), self.operation(t, 'get'))
        comparison = self.equal(t.args[1], value, f'(call {get} (local.get $other) {key})')
        return f'  (func {D}.eq (param $a (ref null {D})) (param $b (ref null {D})) (result i32)\n    (local $d (ref {D})) (local $other (ref {D})) (local $i i32)\n    (if (ref.eq (local.get $a) (local.get $b)) (then (return (i32.const 1))))\n    (if (i32.or (ref.is_null (local.get $a)) (ref.is_null (local.get $b))) (then (return (i32.const 0))))\n    (local.set $d (ref.as_non_null (local.get $a)))\n    (local.set $other (ref.as_non_null (local.get $b)))\n    (if (i32.ne {length} (struct.get {D} $len (local.get $other))) (then (return (i32.const 0))))\n    (block $done (loop $scan\n      (br_if $done (i32.ge_u (local.get $i) {used}))\n      (if (i32.eqz (ref.is_null (array.get {A} {entries} (local.get $i)))) (then\n        (if (i32.eqz (call {contains} (local.get $other) {key})) (then (return (i32.const 0))))\n        (if (i32.eqz {comparison}) (then (return (i32.const 0))))))\n      (local.set $i (i32.add (local.get $i) (i32.const 1))) (br $scan)))\n    (i32.const 1))'
    if op == 'list':
        view = DeclType('view', 'keys').add(t)
        fn = self.operation(view, 'list')
        T = self.types.heap(view)
        return head + f" (result {self.types.valtype(DeclType('list').add(t.args[0]))}) (call {fn} (struct.new {T} {d})))"
    if op == 'to_str':
        k, v = (self.to_string(t.args[0], key, True), self.to_string(t.args[1], value, True))
        return head + f" (result (ref $str)) (local $buf (ref $buf)) (local $i i32) (local $n i32)\n    (local.set $buf (call $rt.buf_new (i32.const 32)))\n    (call $rt.buf_str (local.get $buf) {self.literals.lit('{')})\n    (block $done (loop $scan\n      (br_if $done (i32.ge_u (local.get $i) {used}))\n      (if (i32.eqz (ref.is_null (array.get {A} {entries} (local.get $i)))) (then\n        (if (local.get $n) (then (call $rt.buf_str (local.get $buf) {self.literals.lit(', ')})))\n        (call $rt.buf_str (local.get $buf) {k})\n        (call $rt.buf_str (local.get $buf) {self.literals.lit(': ')})\n        (call $rt.buf_str (local.get $buf) {v})\n        (local.set $n (i32.add (local.get $n) (i32.const 1)))))\n      (local.set $i (i32.add (local.get $i) (i32.const 1))) (br $scan)))\n    (call $rt.buf_str (local.get $buf) {self.literals.lit('}')}) (call $rt.buf_done (local.get $buf)))"
    raise ValueError(f'dict helper {op}')

def emit_key_error(self: SourceEmitter, t: DeclType, value: str) -> str:
    return f'(throw $snek.exc (struct.new $KeyError (global.get $KeyError.vt.inst) (ref.null $str) {self.to_string(t, value, True)}))'

def emit_view_helper(self: SourceEmitter, t: DeclType, op: str) -> str:
    T, D = (self.types.heap(t), self.types.heap(t.args[0]))
    head = f'  (func {T}.{op} (param $v (ref {T}))'
    d = f'(struct.get {T} $dict (local.get $v))'
    if op == 'mapping':
        return head + f' (result (ref {D})) {d})'
    if op == 'len':
        return head + f' (result i32) (struct.get {D} $len {d}))'
    if op == 'contains' and t.name == 'keys':
        fn = self.operation(t.args[0], 'contains')
        return head + f' (param $x {self.types.valtype(emit_element(t))}) (result i32) (call {fn} {d} (local.get $x)))'
    if op == 'to_str':
        lst = self.operation(t, 'list')
        text = self.to_string(DeclType('list').add(emit_element(t)), f'(call {lst} (local.get $v))')
        return head + f" (result (ref $str)) (call $rt.str_concat_n (array.new_fixed $strs 3 {self.literals.lit('dict_' + t.name + '(')} {text} {self.literals.lit(')')})))"
    if op in ('list', 'contains'):
        elem = self.types.valtype(emit_element(t))
        at = self.operation(t.args[0], t.name + '_at')
        value = f'(call {at} {d} (local.get $i))'
        if op == 'list':
            L = self.types.heap(DeclType('list').add(emit_element(t)))
            append = L + '.append'
            array = '$arr.' + L.removeprefix('$list.')
            signature = f' (result (ref {L})) (local $out (ref {L})) (local $i i32)'
            init = f'(local.set $out (struct.new {L} (i32.const 0) (array.new_default {array} (struct.get {D} $len {d}))))'
            action = f'(call {append} (local.get $out) {value})'
            result = '(local.get $out)'
        else:
            signature = f' (param $x {elem}) (result i32) (local $i i32)'
            init = ''
            action = f"(if {self.equal(emit_element(t), value, '(local.get $x)')} (then (return (i32.const 1))))"
            result = '(i32.const 0)'
        return head + signature + f'\n    {init}\n    (block $done (loop $scan\n      (br_if $done (i32.ge_u (local.get $i) (struct.get {D} $used {d})))\n      (if (i32.eqz (ref.is_null (array.get {D}.entries (struct.get {D} $entries {d}) (local.get $i)))) (then {action}))\n      (local.set $i (i32.add (local.get $i) (i32.const 1))) (br $scan)))\n    {result})'
    raise ValueError(f'dict view helper {op}')

def emit_list_builtin(emitter: SourceEmitter, t: DeclType, op: str) -> str:
    e = emit_element(t)
    L = emitter.types.heap(t)
    A = '$arr.' + L.removeprefix('$list.')
    R = emitter.types.valtype(e)
    get = 'array.get_u' if e.same(DeclType('bool')) else 'array.get'

    def load(index: str, array: str='$data') -> str:
        code = f'({get} {A} (local.get {array}) {index})'
        return f'(ref.as_non_null {code})' if e.reference() else code

    def less(a: str, b: str) -> str:
        if e.same(DeclType('str')):
            return f'(i32.lt_s (call $rt.str_cmp {a} {b}) (i32.const 0))'
        n = 'i32' if e.same(DeclType('bool')) else e.kind
        cmp = 'lt' if e.kind in ('bool', 'i32', 'i64', 'f32', 'f64') and e.kind in ('f32', 'f64') else 'lt_s'
        return f'({n}.{cmp} {a} {b})'
    head = f'  (func {L}.{op} (param $xs (ref {L}))'
    setup = f'(local.set $n (struct.get {L} $len (local.get $xs)))\n    (local.set $data (struct.get {L} $data (local.get $xs)))'
    if op.startswith('min') or op.startswith('max'):
        kind = op.split('_')[0]
        optional = op.endswith('optional')
        result = emitter.types.valtype(DeclType('optional').add(e)) if optional else R
        default = '_' in op
        param = f' (param $fallback {result})' if default else ''
        empty = '(return (local.get $fallback))' if default else emitter.error('ValueError', f'{kind}() iterable argument is empty')
        cmp = less('(local.get $value)', '(local.get $best)') if kind == 'min' else less('(local.get $best)', '(local.get $value)')
        return head + f"{param} (result {result})\n    (local $n i32) (local $i i32) (local $data (ref {A})) (local $best {R}) (local $value {R})\n    {setup}\n    (if (i32.eqz (local.get $n)) (then {empty}))\n    (local.set $best {load('(i32.const 0)', '$data')})\n    (local.set $i (i32.const 1))\n    (block $done (loop $next\n      (br_if $done (i32.ge_u (local.get $i) (local.get $n)))\n      (local.set $value {load('(local.get $i)', '$data')})\n      (if {cmp} (then (local.set $best (local.get $value))))\n      (local.set $i (i32.add (local.get $i) (i32.const 1))) (br $next)))\n    (local.get $best))"
    if op.startswith('sum_'):
        ret = DeclType(op.removeprefix('sum_'))
        N = ret.kind
        value = load('(local.get $i)', '$data')
        source = DeclType('i32') if e.same(DeclType('bool')) else e
        if not source.same(ret):
            conv = {('i32', 'i64'): 'i64.extend_i32_s', ('i32', 'f64'): 'f64.convert_i32_s', ('f32', 'f64'): 'f64.promote_f32'}[source.kind, ret.kind]
            value = f'({conv} {value})'
        compensated = e.kind in ('f32', 'f64')
        locals_ = '(local $c f64) (local $v f64) (local $next f64)' if compensated else ''
        add = f'(local.set $total ({N}.add (local.get $total) {value}))'
        if compensated:
            add = f'(local.set $v {value})\n      (local.set $next (f64.add (local.get $total) (local.get $v)))\n      (local.set $c (f64.add (local.get $c)\n        (if (result f64) (f64.ge (f64.abs (local.get $total)) (f64.abs (local.get $v)))\n          (then (f64.add (f64.sub (local.get $total) (local.get $next)) (local.get $v)))\n          (else (f64.add (f64.sub (local.get $v) (local.get $next)) (local.get $total))))))\n      (local.set $total (local.get $next))'
        finish = '(if (i32.and (f64.ne (local.get $c) (f64.const 0)) (f64.lt (f64.abs (local.get $c)) (f64.const inf))) (then (local.set $total (f64.add (local.get $total) (local.get $c)))))' if compensated else ''
        return head + f' (param $total {N}) (result {N})\n    (local $n i32) (local $i i32) (local $data (ref {A})) {locals_}\n    {setup}\n    (block $done (loop $next_item\n      (br_if $done (i32.ge_u (local.get $i) (local.get $n)))\n      {add}\n      (local.set $i (i32.add (local.get $i) (i32.const 1))) (br $next_item)))\n    {finish} (local.get $total))'
    assert op == 'sorted', op
    left, right = (load('(local.get $i)', '$src'), load('(local.get $j)', '$src'))
    choose_right = f'(if (result i32) (local.get $reverse) (then {less(left, right)}) (else {less(right, left)}))'
    return head + f''' (param $reverse i32) (result (ref {L}))
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
    (struct.new {L} (local.get $n) (local.get $src)))'''

def emit_keyed_builtin(emitter: SourceEmitter, name: str, elem: DeclType, key: DeclType, op: str, result: DeclType) -> str:
    L = emitter.types.heap(DeclType('list').add(elem))
    A = '$arr.' + L.removeprefix('$list.')
    K = key.args[-1]
    KL = emitter.types.heap(DeclType('list').add(K))
    KA = '$arr.' + KL.removeprefix('$list.')
    C = emitter.types.heap(key)
    V, R = (emitter.types.valtype(elem), emitter.types.valtype(K))

    def load(ty: DeclType, arr_type: str, arr: str, index: str) -> str:
        code = f"({('array.get_u' if ty.same(DeclType('bool')) else 'array.get')} {arr_type} {arr} {index})"
        return f'(ref.as_non_null {code})' if ty.reference() and (not ty.kind in ('optional',)) else code

    def value(arr: str, index: str) -> str:
        return load(elem, A, f'(local.get {arr})', index)

    def key_at(arr: str, index: str) -> str:
        return load(K, KA, f'(local.get {arr})', index)

    def call_key(value: str) -> str:
        return f'(call_ref {C}.sig (struct.get {C} $env (local.get $key)) {value} (struct.get {C} $fn (local.get $key)))'

    def less(a: str, b: str) -> str:
        if K.same(DeclType('str')):
            return f'(i32.lt_s (call $rt.str_cmp {a} {b}) (i32.const 0))'
        n = 'i32' if K.same(DeclType('bool')) else K.kind
        cmp = 'lt' if K.kind in ('bool', 'i32', 'i64', 'f32', 'f64') and K.kind in ('f32', 'f64') else 'lt_s'
        return f'({n}.{cmp} {a} {b})'
    head = f'  (func {name} (param $xs (ref {L})) (param $key (ref {C}))'
    if op != 'sorted':
        kind = op.split('_')[0]
        ret = emitter.types.valtype(result)
        param = f' (param $fallback {ret})' if '_' in op else ''
        empty = '(return (local.get $fallback))' if param != '' else emitter.error('ValueError', f'{kind}() iterable argument is empty')
        length = f'(struct.get {L} $len (local.get $xs))'
        data = f'(struct.get {L} $data (local.get $xs))'
        item = load(elem, A, data, '(local.get $i)')
        cmp = less('(local.get $candidate)', '(local.get $best_key)') if kind == 'min' else less('(local.get $best_key)', '(local.get $candidate)')
        return head + f"{param} (result {ret})\n    (local $i i32) (local $best {V}) (local $value {V}) (local $best_key {R}) (local $candidate {R})\n    (if (i32.eqz {length}) (then {empty}))\n    (local.set $best {item})\n    (local.set $best_key {call_key('(local.get $best)')})\n    (local.set $i (i32.const 1))\n    (block $done (loop $next\n      (br_if $done (i32.ge_u (local.get $i) {length}))\n      (local.set $value {item})\n      (local.set $candidate {call_key('(local.get $value)')})\n      (if {cmp} (then (local.set $best (local.get $value)) (local.set $best_key (local.get $candidate))))\n      (local.set $i (i32.add (local.get $i) (i32.const 1))) (br $next)))\n    (local.get $best))"
    left = value('$src', '(local.get $i)')
    right = value('$src', '(local.get $j)')
    lk = key_at('$keys', '(local.get $i)')
    rk = key_at('$keys', '(local.get $j)')
    choose_right = f'(if (result i32) (local.get $reverse) (then {less(lk, rk)}) (else {less(rk, lk)}))'
    return head + f''' (param $reverse i32) (result (ref {L}))
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
    (struct.new {L} (local.get $n) (local.get $src)))'''

def emit_read_helper(emitter: SourceEmitter, name: str='snek.read_bytes', error_class: str='RuntimeError') -> str:
    read_error = emitter.error(error_class, 'stdin read failed')
    return f'''
  (func ${name} (result (ref $list.i32))
    (local $out (ref $list.i32)) (local $n i32) (local $i i32)
    ;; Input and output share the fixed WASI scratch page; flush before reusing it.
    (call $rt.flush)
    (local.set $out (struct.new $list.i32 (i32.const 0) (array.new_default $arr.i32 (i32.const 0))))
    (block $done (loop $read
      (i32.store (i32.const 0) (i32.const 1024))
      (i32.store (i32.const 4) (i32.const 64512))
      (if (call $wasi.fd_read (i32.const 0) (i32.const 0) (i32.const 1) (i32.const 8))
        (then {read_error}))
      (local.set $n (i32.load (i32.const 8)))
      (if (i32.gt_u (local.get $n) (i32.const 64512)) (then {read_error}))
      (br_if $done (i32.eqz (local.get $n)))
      (local.set $i (i32.const 0))
      (loop $copy
        (call $list.i32.append (local.get $out) (i32.load8_u offset=1024 (local.get $i)))
        (local.set $i (i32.add (local.get $i) (i32.const 1)))
        (br_if $copy (i32.lt_u (local.get $i) (local.get $n))))
      (br $read)))
    (local.get $out))
'''

def emit_byte_io(emitter: SourceEmitter) -> str:
    bad_byte = emitter.error('ValueError', 'byte values must be in range 0..255')
    write_error = emitter.error('RuntimeError', 'stdout write failed')
    return emit_read_helper(emitter) + f'''
  (func $snek.write_bytes (param $values (ref $list.i32))
    (local $data (ref $arr.i32)) (local $len i32) (local $i i32)
    (local $n i32) (local $ptr i32) (local $written i32)
    (local.set $data (struct.get $list.i32 $data (local.get $values)))
    (local.set $len (struct.get $list.i32 $len (local.get $values)))
    ;; Reject all invalid values before writing any bytes from this call.
    (block $checked (loop $check
      (br_if $checked (i32.ge_u (local.get $i) (local.get $len)))
      (if (i32.gt_u (array.get $arr.i32 (local.get $data) (local.get $i)) (i32.const 255))
        (then {bad_byte}))
      (local.set $i (i32.add (local.get $i) (i32.const 1))) (br $check)))
    (call $rt.flush)
    (local.set $i (i32.const 0))
    (block $done (loop $chunk
      (br_if $done (i32.ge_u (local.get $i) (local.get $len)))
      (local.set $n (i32.const 0))
      (loop $copy
        (i32.store8 offset=1024 (local.get $n) (array.get $arr.i32 (local.get $data) (local.get $i)))
        (local.set $i (i32.add (local.get $i) (i32.const 1)))
        (local.set $n (i32.add (local.get $n) (i32.const 1)))
        (br_if $copy (i32.and (i32.lt_u (local.get $i) (local.get $len)) (i32.lt_u (local.get $n) (i32.const 64512)))))
      (local.set $ptr (i32.const 1024))
      (loop $write
        (i32.store (i32.const 0) (local.get $ptr))
        (i32.store (i32.const 4) (local.get $n))
        (if (call $wasi.fd_write (i32.const 1) (i32.const 0) (i32.const 1) (i32.const 8))
          (then {write_error}))
        (local.set $written (i32.load (i32.const 8)))
        (if (i32.or (i32.eqz (local.get $written)) (i32.gt_u (local.get $written) (local.get $n)))
          (then {write_error}))
        (local.set $ptr (i32.add (local.get $ptr) (local.get $written)))
        (local.set $n (i32.sub (local.get $n) (local.get $written)))
        (br_if $write (local.get $n)))
      (br $chunk))))
'''
