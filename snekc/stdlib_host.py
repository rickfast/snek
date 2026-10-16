"""GC adapters for CLI configuration, clocks, timers and secure randomness."""
from . import ir, wasi_bindings
from .types import CoroutineT, F64, I32, ListT, NONE, STR


def machines(emitter):
    if "sleep" not in emitter.wasi_features:
        return []
    seconds = ir.LocalGet(F64, "seconds", F64)
    if emitter.component:
        body = [ir.ExprStmt(ir.Call(NONE, "lib.timer-start", [seconds])), ir.Yield(None),
                ir.ExprStmt(ir.Call(NONE, "lib.timer-result", []))]
    else:
        body = [ir.ExprStmt(ir.Call(NONE, "lib.sleep-core", [seconds]))]
    return [emitter.emit_machine(ir.Func("snek.lib.sleep", [("seconds", F64)], CoroutineT(NONE), {}, body, machine=True))]


def core_imports(features):
    bindings = {}
    for feature in ("arguments", "environment"):
        if feature in features:
            prefix = "args" if feature == "arguments" else "environ"
            bindings[prefix + "_sizes_get"] = "(param i32 i32) (result i32)"
            bindings[prefix + "_get"] = "(param i32 i32) (result i32)"
    if set(features) & {"system-clock", "monotonic-clock"}:
        bindings["clock_time_get"] = "(param i32 i64 i32) (result i32)"
    if "sleep" in features:
        bindings["poll_oneoff"] = "(param i32 i32 i32 i32) (result i32)"
    if "random" in features:
        bindings["random_get"] = "(param i32 i32) (result i32)"
    return '\n'.join(f'(import "wasi_snapshot_preview1" "{name}" (func $host.{name} {sig}))'
                     for name, sig in bindings.items())


def helpers(emitter):
    features = emitter.wasi_features
    arena = wasi_bindings.needs_arena(features)
    error = emitter.raise_code("OSError", "host operation failed")
    encoding = emitter.raise_code("OSError", "host returned invalid UTF-8")
    parts = []
    reset = ""
    if "arguments" in features:
        emitter.heap(ListT(STR))
        parts.append('(global $lib.args (mut (ref null $list.str)) (ref.null $list.str))')
        reset = '(global.set $lib.args (ref.null $list.str))'
    if arena:
        parts.append('''
  (func $abi.reset (call $io.lib-reset-arena))
  (func $abi.alloc (param $n i32) (param $align i32) (result i32)
    (call $io.lib-alloc (local.get $n) (local.get $align)))
''' if emitter.component else wasi_bindings.MEMORY.read_text())
        reset += ' (call $abi.reset)'
        parts.append(f'''
  (func $abi.zlen (param $ptr i32) (param $end i32) (result i32) (local $n i32)
    (loop $scan
      (if (i32.ge_u (i32.add (local.get $ptr) (local.get $n)) (local.get $end)) (then {error}))
      (if (i32.eqz (i32.load8_u (i32.add (local.get $ptr) (local.get $n)))) (then (return (local.get $n))))
      (local.set $n (i32.add (local.get $n) (i32.const 1))) (br $scan)) (unreachable))

  (func $abi.string (param $ptr i32) (param $n i32) (result (ref $str))
    (local $bytes (ref $u8s)) (local $i i32) (local $b i32) (local $width i32)
    (local $j i32) (local $next i32)
    ;; Reject malformed POSIX byte strings before creating a Snek UTF-8 str.
    (block $valid (loop $validate
      (br_if $valid (i32.ge_u (local.get $i) (local.get $n)))
      (local.set $b (i32.load8_u (i32.add (local.get $ptr) (local.get $i))))
      (if (i32.lt_u (local.get $b) (i32.const 128))
        (then (local.set $i (i32.add (local.get $i) (i32.const 1))) (br $validate)))
      (if (i32.or (i32.lt_u (local.get $b) (i32.const 194)) (i32.gt_u (local.get $b) (i32.const 244))) (then {encoding}))
      (local.set $width (if (result i32) (i32.lt_u (local.get $b) (i32.const 224)) (then (i32.const 2))
        (else (if (result i32) (i32.lt_u (local.get $b) (i32.const 240)) (then (i32.const 3)) (else (i32.const 4))))))
      (if (i32.gt_u (i32.add (local.get $i) (local.get $width)) (local.get $n)) (then {encoding}))
      (local.set $next (i32.load8_u (i32.add (local.get $ptr) (i32.add (local.get $i) (i32.const 1)))))
      (if (i32.or (i32.and (i32.eq (local.get $b) (i32.const 224)) (i32.lt_u (local.get $next) (i32.const 160)))
                  (i32.and (i32.eq (local.get $b) (i32.const 237)) (i32.ge_u (local.get $next) (i32.const 160)))) (then {encoding}))
      (if (i32.or (i32.and (i32.eq (local.get $b) (i32.const 240)) (i32.lt_u (local.get $next) (i32.const 144)))
                  (i32.and (i32.eq (local.get $b) (i32.const 244)) (i32.ge_u (local.get $next) (i32.const 144)))) (then {encoding}))
      (local.set $j (i32.const 1))
      (loop $continuations
        (local.set $next (i32.load8_u (i32.add (local.get $ptr) (i32.add (local.get $i) (local.get $j)))))
        (if (i32.ne (i32.and (local.get $next) (i32.const 192)) (i32.const 128)) (then {encoding}))
        (local.set $j (i32.add (local.get $j) (i32.const 1)))
        (br_if $continuations (i32.lt_u (local.get $j) (local.get $width))))
      (local.set $i (i32.add (local.get $i) (local.get $width))) (br $validate)))
    (local.set $bytes (array.new_default $u8s (local.get $n)))
    (local.set $i (i32.const 0))
    (block $done (loop $copy
      (br_if $done (i32.ge_u (local.get $i) (local.get $n)))
      (array.set $u8s (local.get $bytes) (local.get $i) (i32.load8_u (i32.add (local.get $ptr) (local.get $i))))
      (local.set $i (i32.add (local.get $i) (i32.const 1))) (br $copy)))
    (call $rt.mkstr (local.get $bytes) (i32.const -1)))
''')
    parts.append(f'(func $lib.reset {reset})')
    for feature in ("arguments", "environment"):
        if feature not in features:
            continue
        if not emitter.component:
            prefix = "args" if feature == "arguments" else "environ"
            stride = 8 if feature == "arguments" else 16
            fields = '''(i32.store (local.get $entry) (local.get $ptr))
        (i32.store offset=4 (local.get $entry) (local.get $len))''' if feature == "arguments" else f'''
        (local.set $keylen (i32.const 0))
        (block $equal (loop $key
          (if (i32.ge_u (local.get $keylen) (local.get $len)) (then {error}))
          (br_if $equal (i32.eq (i32.load8_u (i32.add (local.get $ptr) (local.get $keylen))) (i32.const 61)))
          (local.set $keylen (i32.add (local.get $keylen) (i32.const 1))) (br $key)))
        (i32.store (local.get $entry) (local.get $ptr))
        (i32.store offset=4 (local.get $entry) (local.get $keylen))
        (i32.store offset=8 (local.get $entry) (i32.add (local.get $ptr) (i32.add (local.get $keylen) (i32.const 1))))
        (i32.store offset=12 (local.get $entry) (i32.sub (local.get $len) (i32.add (local.get $keylen) (i32.const 1))))'''
            parts.append(f'''
  (func $io.lib-{feature} (param $out i32)
    (local $count i32) (local $size i32) (local $pointers i32) (local $buffer i32)
    (local $table i32) (local $i i32) (local $ptr i32) (local $len i32) (local $entry i32) (local $keylen i32)
    (if (call $host.{prefix}_sizes_get (i32.const 256) (i32.const 260)) (then {error}))
    (local.set $count (i32.load (i32.const 256))) (local.set $size (i32.load (i32.const 260)))
    (if (i32.or (i32.gt_u (local.get $count) (i32.const {2147483647 // stride}))
                (i32.gt_u (local.get $size) (i32.const 2147483647))) (then {error}))
    (local.set $pointers (call $abi.alloc (i32.mul (local.get $count) (i32.const 4)) (i32.const 4)))
    (local.set $buffer (call $abi.alloc (local.get $size) (i32.const 1)))
    ;; Empty vectors have null allocations; Node's WASI rejects get(0, 0).
    (if (local.get $count)
      (then (if (call $host.{prefix}_get (local.get $pointers) (local.get $buffer)) (then {error}))))
    (local.set $table (call $abi.alloc (i32.mul (local.get $count) (i32.const {stride})) (i32.const 4)))
    (block $done (loop $convert
      (br_if $done (i32.ge_u (local.get $i) (local.get $count)))
      (local.set $ptr (i32.load (i32.add (local.get $pointers) (i32.mul (local.get $i) (i32.const 4)))))
      (if (i32.or (i32.lt_u (local.get $ptr) (local.get $buffer))
                  (i32.ge_u (local.get $ptr) (i32.add (local.get $buffer) (local.get $size)))) (then {error}))
      (local.set $len (call $abi.zlen (local.get $ptr) (i32.add (local.get $buffer) (local.get $size))))
      (local.set $entry (i32.add (local.get $table) (i32.mul (local.get $i) (i32.const {stride}))))
      {fields}
      (local.set $i (i32.add (local.get $i) (i32.const 1))) (br $convert)))
    (i32.store (local.get $out) (local.get $table)) (i32.store offset=4 (local.get $out) (local.get $count)))
''')
    if "arguments" in features:
        parts.append('''
  (func $snek.lib.argv (result (ref $list.str))
    (local $out (ref $list.str)) (local $ptr i32) (local $n i32) (local $i i32) (local $entry i32)
    (if (i32.eqz (ref.is_null (global.get $lib.args))) (then (return (ref.as_non_null (global.get $lib.args)))))
    (call $abi.reset) (call $io.lib-arguments (i32.const 64))
    (local.set $ptr (i32.load (i32.const 64))) (local.set $n (i32.load (i32.const 68)))
    (local.set $out (struct.new $list.str (i32.const 0) (array.new_default $arr.str (i32.const 0))))
    (block $done (loop $copy
      (br_if $done (i32.ge_u (local.get $i) (local.get $n)))
      (local.set $entry (i32.add (local.get $ptr) (i32.mul (local.get $i) (i32.const 8))))
      (call $list.str.append (local.get $out) (call $abi.string (i32.load (local.get $entry)) (i32.load offset=4 (local.get $entry))))
      (local.set $i (i32.add (local.get $i) (i32.const 1))) (br $copy)))
    (call $abi.reset) (global.set $lib.args (local.get $out)) (local.get $out))
''')
    if "environment" in features:
        parts.append('''
  (func $snek.lib.getenv (param $key (ref $str)) (param $default (ref null $str)) (result (ref null $str))
    (local $ptr i32) (local $n i32) (local $i i32) (local $entry i32) (local $value (ref $str))
    (call $abi.reset) (call $io.lib-environment (i32.const 64))
    (local.set $ptr (i32.load (i32.const 64))) (local.set $n (i32.load (i32.const 68)))
    (block $done (loop $find
      (br_if $done (i32.ge_u (local.get $i) (local.get $n)))
      (local.set $entry (i32.add (local.get $ptr) (i32.mul (local.get $i) (i32.const 16))))
      (if (call $rt.str_eq (local.get $key) (call $abi.string (i32.load (local.get $entry)) (i32.load offset=4 (local.get $entry))))
        (then
          (local.set $value (call $abi.string (i32.load offset=8 (local.get $entry)) (i32.load offset=12 (local.get $entry))))
          (call $abi.reset) (return (local.get $value))))
      (local.set $i (i32.add (local.get $i) (i32.const 1))) (br $find)))
    (call $abi.reset) (local.get $default))
  (func $snek.lib.getenv-required (param $key (ref $str)) (param $default (ref null $str)) (result (ref $str))
    (ref.as_non_null (call $snek.lib.getenv (local.get $key) (local.get $default))))
''')
    for feature, id_ in (("system-clock", 0), ("monotonic-clock", 1)):
        if feature not in features:
            continue
        if not emitter.component:
            result = '(result i64)' if id_ else '(param $out i32)'
            finish = '(i64.load (i32.const 80))' if id_ else '''
    (local.set $n (i64.load (i32.const 80)))
    (i64.store (local.get $out) (i64.div_u (local.get $n) (i64.const 1000000000)))
    (i32.store offset=8 (local.get $out) (i32.wrap_i64 (i64.rem_u (local.get $n) (i64.const 1000000000))))'''
            parts.append(f'''(func $io.lib-{'monotonic' if id_ else 'system'}-now {result} (local $n i64)
    (if (call $host.clock_time_get (i32.const {id_}) (i64.const 1) (i32.const 80)) (then {error})) {finish})''')
        parts.append('''(func $snek.lib.monotonic (result f64)
    (f64.div (f64.convert_i64_u (call $io.lib-monotonic-now)) (f64.const 1000000000)))''' if id_ else f'''
  (func $snek.lib.time (result f64)
    (call $io.lib-system-now (i32.const 80))
    (if (i32.ge_u (i32.load (i32.const 88)) (i32.const 1000000000)) (then {error}))
    (f64.add (f64.convert_i64_s (i64.load (i32.const 80)))
      (f64.div (f64.convert_i32_u (i32.load (i32.const 88))) (f64.const 1000000000))))
''')
    if "sleep" in features:
        negative = emitter.raise_code("ValueError", "sleep length must be non-negative and not NaN")
        too_large = emitter.raise_code("OverflowError", "sleep length is too large")
        parts.append(f'''
  (func $lib.duration (param $s f64) (result i64) (local $ns f64)
    (if (i32.or (f64.ne (local.get $s) (local.get $s)) (f64.lt (local.get $s) (f64.const 0))) (then {negative}))
    (local.set $ns (f64.ceil (f64.mul (local.get $s) (f64.const 1000000000))))
    (if (f64.ge (local.get $ns) (f64.const 18446744073709551616)) (then {too_large}))
    (i64.trunc_f64_u (local.get $ns)))
''')
        if emitter.component:
            parts.append(timer_runtime(error))
        else:
            parts.append(f'''
  (func $lib.sleep-core (param $s f64) (local $ns i64)
    (local.set $ns (call $lib.duration (local.get $s)))
    (call $rt.flush)
    (i64.store (i32.const 128) (i64.const 0))
    (i32.store8 (i32.const 136) (i32.const 0))
    (i32.store (i32.const 144) (i32.const 1))
    (i64.store (i32.const 152) (local.get $ns))
    (i64.store (i32.const 160) (i64.const 1))
    (i32.store16 (i32.const 168) (i32.const 0))
    (if (call $host.poll_oneoff (i32.const 128) (i32.const 192) (i32.const 1) (i32.const 240)) (then {error}))
    (if (i32.or (i32.ne (i32.load (i32.const 240)) (i32.const 1)) (i32.load16_u (i32.const 200))) (then {error})))
''')
    if "random" in features:
        emitter.heap(ListT(I32))
        parts.append(random_helpers(emitter, error))
    if not emitter.component or "sleep" not in features:
        parts.append('(func $lib.timer-pump (param i32 i32 i32) (result i32) (i32.const 0))')
    return '\n'.join(parts)


def timer_runtime(error):
    return f'''
  (global $lib.timer-stage (mut i32) (i32.const 0))
  (global $lib.timer-ns (mut i64) (i64.const 0))
  (global $lib.timer-handle (mut i32) (i32.const 0))
  (global $lib.timer-status (mut i32) (i32.const 2))
  (func $lib.timer-start (param $s f64)
    (if (global.get $lib.timer-stage) (then (unreachable)))
    (global.set $lib.timer-ns (call $lib.duration (local.get $s)))
    (call $rt.flush) (global.set $lib.timer-stage (i32.const 1)))
  (func $lib.timer-result
    (if (i32.ne (global.get $lib.timer-stage) (i32.const 3)) (then (unreachable)))
    (global.set $lib.timer-stage (i32.const 0))
    (if (i32.ne (global.get $lib.timer-status) (i32.const 2)) (then {error})))
  (func $lib.timer-drop
    (call $io.join (global.get $lib.timer-handle) (i32.const 0))
    (call $io.lib-subtask-drop (global.get $lib.timer-handle))
    (global.set $lib.timer-handle (i32.const 0)))
  (func $lib.timer-pump (param $event i32) (param $handle i32) (param $payload i32) (result i32)
    (local $packed i32)
    (if (i32.eqz (global.get $lib.timer-stage)) (then (return (i32.const 0))))
    (if (i32.and (global.get $io.cancelled) (i32.eqz (global.get $io.injected)))
      (then
        (if (global.get $lib.timer-handle)
          (then
            (call $io.join (global.get $lib.timer-handle) (i32.const 0))
            (drop (call $io.lib-subtask-cancel (global.get $lib.timer-handle)))
            (call $lib.timer-drop)))
        (global.set $lib.timer-stage (i32.const 0)) (return (i32.const 0))))
    (if (i32.eq (global.get $lib.timer-stage) (i32.const 3)) (then (return (i32.const 0))))
    (if (i32.eq (global.get $lib.timer-stage) (i32.const 1))
      (then
        (local.set $packed (call $io.lib-wait-for (global.get $lib.timer-ns)))
        (global.set $lib.timer-handle (i32.shr_u (local.get $packed) (i32.const 4)))
        (if (global.get $lib.timer-handle)
          (then (call $io.join (global.get $lib.timer-handle) (global.get $io.set))
            (global.set $lib.timer-stage (i32.const 2)) (return (i32.const 1))))
        (global.set $lib.timer-status (i32.and (local.get $packed) (i32.const 15)))
        (global.set $lib.timer-stage (i32.const 3)) (return (i32.const 0))))
    (if (i32.or (i32.ne (local.get $event) (i32.const 1)) (i32.ne (local.get $handle) (global.get $lib.timer-handle)))
      (then (return (i32.const 1))))
    (if (i32.lt_u (local.get $payload) (i32.const 2)) (then (return (i32.const 1))))
    (global.set $lib.timer-status (local.get $payload))
    (call $lib.timer-drop) (global.set $lib.timer-stage (i32.const 3)) (i32.const 0))
'''


def random_helpers(emitter, error):
    negative = emitter.raise_code("ValueError", "number of bytes must be non-negative")
    bound = emitter.raise_code("ValueError", "upper bound must be positive")
    too_large = emitter.raise_code("OverflowError", "hex token is too large")
    core = '' if emitter.component else f'''
  (func $io.lib-random (param $n i64) (param $out i32) (local $ptr i32)
    (if (i64.gt_u (local.get $n) (i64.const 64512)) (then {error}))
    (local.set $ptr (call $abi.alloc (i32.wrap_i64 (local.get $n)) (i32.const 1)))
    (if (call $host.random_get (local.get $ptr) (i32.wrap_i64 (local.get $n))) (then {error}))
    (i32.store (local.get $out) (local.get $ptr)) (i32.store offset=4 (local.get $out) (i32.wrap_i64 (local.get $n))))
'''
    return core + f'''
  (func $snek.lib.token_bytes (param $n i32) (result (ref $list.i32))
    (local $data (ref $arr.i32)) (local $i i32) (local $j i32) (local $request i32) (local $got i32) (local $ptr i32)
    (if (i32.lt_s (local.get $n) (i32.const 0)) (then {negative}))
    (local.set $data (array.new_default $arr.i32 (local.get $n)))
    (block $done (loop $random
      (br_if $done (i32.ge_u (local.get $i) (local.get $n)))
      (local.set $request (i32.sub (local.get $n) (local.get $i)))
      (if (i32.gt_u (local.get $request) (i32.const 64512)) (then (local.set $request (i32.const 64512))))
      (call $abi.reset) (call $io.lib-random (i64.extend_i32_u (local.get $request)) (i32.const 64))
      (local.set $ptr (i32.load (i32.const 64))) (local.set $got (i32.load (i32.const 68)))
      (if (i32.or (i32.eqz (local.get $got)) (i32.gt_u (local.get $got) (local.get $request))) (then {error}))
      (local.set $j (i32.const 0))
      (loop $copy
        (array.set $arr.i32 (local.get $data) (local.get $i) (i32.load8_u (i32.add (local.get $ptr) (local.get $j))))
        (local.set $j (i32.add (local.get $j) (i32.const 1))) (local.set $i (i32.add (local.get $i) (i32.const 1)))
        (br_if $copy (i32.lt_u (local.get $j) (local.get $got))))
      (br $random)))
    (call $abi.reset) (struct.new $list.i32 (local.get $n) (local.get $data)))

  (func $lib.hex-digit (param $n i32) (result i32)
    (i32.add (local.get $n) (if (result i32) (i32.lt_u (local.get $n) (i32.const 10)) (then (i32.const 48)) (else (i32.const 87)))))
  (func $snek.lib.token_hex (param $n i32) (result (ref $str))
    (local $values (ref $list.i32)) (local $data (ref $arr.i32)) (local $out (ref $buf)) (local $i i32) (local $b i32)
    (if (i32.gt_s (local.get $n) (i32.const 1073741823)) (then {too_large}))
    (local.set $values (call $snek.lib.token_bytes (local.get $n)))
    (local.set $data (struct.get $list.i32 $data (local.get $values)))
    (local.set $out (call $rt.buf_new (i32.mul (local.get $n) (i32.const 2))))
    (block $done (loop $hex
      (br_if $done (i32.ge_u (local.get $i) (local.get $n)))
      (local.set $b (array.get $arr.i32 (local.get $data) (local.get $i)))
      (call $rt.buf_cp (local.get $out) (call $lib.hex-digit (i32.shr_u (local.get $b) (i32.const 4))))
      (call $rt.buf_cp (local.get $out) (call $lib.hex-digit (i32.and (local.get $b) (i32.const 15))))
      (local.set $i (i32.add (local.get $i) (i32.const 1))) (br $hex)))
    (call $rt.buf_done (local.get $out)))

  (func $snek.lib.randbelow (param $n i32) (result i32)
    (local $limit i64) (local $sample i64) (local $values (ref $list.i32)) (local $data (ref $arr.i32)) (local $i i32)
    (if (i32.le_s (local.get $n) (i32.const 0)) (then {bound}))
    (local.set $limit (i64.sub (i64.const 4294967296) (i64.rem_u (i64.const 4294967296) (i64.extend_i32_u (local.get $n)))))
    (loop $sample
      (local.set $values (call $snek.lib.token_bytes (i32.const 4)))
      (local.set $data (struct.get $list.i32 $data (local.get $values)))
      (local.set $sample (i64.const 0)) (local.set $i (i32.const 0))
      (loop $pack
        (local.set $sample (i64.or (local.get $sample)
          (i64.shl (i64.extend_i32_u (array.get $arr.i32 (local.get $data) (local.get $i))) (i64.extend_i32_u (i32.mul (local.get $i) (i32.const 8))))))
        (local.set $i (i32.add (local.get $i) (i32.const 1))) (br_if $pack (i32.lt_u (local.get $i) (i32.const 4))))
      (br_if $sample (i64.ge_u (local.get $sample) (local.get $limit))))
    (i32.wrap_i64 (i64.rem_u (local.get $sample) (i64.extend_i32_u (local.get $n)))))
'''
