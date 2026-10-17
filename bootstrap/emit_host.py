"""WASI host and stream adapter generation in Snek."""

from __future__ import annotations

from snek import i32

from bootstrap.declarations import DeclType

from bootstrap.emit_program import SourceEmitter
from bootstrap.emit_data import emit_memory_source
from snekc.runtime_abi import abi_component_base_imports, abi_component_input_imports, abi_component_library_imports, abi_component_arena_imports, abi_needs_arena, abi_host_imports

def emit_needs_arena(features: list[str]) -> bool:
    return abi_needs_arena(features)

def emit_component_imports(stdin: bool, features: list[str]) -> str:
    bindings = abi_component_base_imports()
    if stdin:
        bindings.update(abi_component_input_imports())
    libraries = abi_component_library_imports()
    for feature in sorted(features): bindings.update(libraries[feature])
    if emit_needs_arena(features): bindings.update(abi_component_arena_imports())
    lines: list[str] = []
    for name, sig in bindings.items(): lines.append(f'(import "snek:component" "{name}" (func $io.{name} {sig}))')
    lines.append('(import "snek:component" "memory" (memory 1))')
    return "\n".join(lines)

def emit_host_core_imports(features: list[str]) -> str:
    bindings = abi_host_imports(features)
    out: list[str] = []
    for name, sig in bindings.items():
        out.append(f'(import "wasi_snapshot_preview1" "{name}" (func $host.{name} {sig}))')
    return '\n'.join(out)

def emit_host_helpers(emitter: SourceEmitter) -> str:
    features = emitter.features
    arena = emit_needs_arena(features)
    error = emitter.error('OSError', 'host operation failed')
    encoding = emitter.error('OSError', 'host returned invalid UTF-8')
    parts: list[str] = []
    reset = ''
    if 'arguments' in features:
        emitter.types.heap(DeclType('list').add(DeclType('str')))
        parts.append('(global $lib.args (mut (ref null $list.str)) (ref.null $list.str))')
        reset = '(global.set $lib.args (ref.null $list.str))'
    if arena:
        parts.append('\n  (func $abi.reset (call $io.lib-reset-arena))\n  (func $abi.alloc (param $n i32) (param $align i32) (result i32)\n    (call $io.lib-alloc (local.get $n) (local.get $align)))\n' if emitter.component else emit_memory_source())
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
    for feature in ('arguments', 'environment'):
        if feature not in features:
            continue
        if not emitter.component:
            prefix = 'args' if feature == 'arguments' else 'environ'
            stride = 8 if feature == 'arguments' else 16
            fields = '(i32.store (local.get $entry) (local.get $ptr))\n        (i32.store offset=4 (local.get $entry) (local.get $len))' if feature == 'arguments' else f'\n        (local.set $keylen (i32.const 0))\n        (block $equal (loop $key\n          (if (i32.ge_u (local.get $keylen) (local.get $len)) (then {error}))\n          (br_if $equal (i32.eq (i32.load8_u (i32.add (local.get $ptr) (local.get $keylen))) (i32.const 61)))\n          (local.set $keylen (i32.add (local.get $keylen) (i32.const 1))) (br $key)))\n        (i32.store (local.get $entry) (local.get $ptr))\n        (i32.store offset=4 (local.get $entry) (local.get $keylen))\n        (i32.store offset=8 (local.get $entry) (i32.add (local.get $ptr) (i32.add (local.get $keylen) (i32.const 1))))\n        (i32.store offset=12 (local.get $entry) (i32.sub (local.get $len) (i32.add (local.get $keylen) (i32.const 1))))'
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
    (if (local.get $count) (then (if (call $host.{prefix}_get (local.get $pointers) (local.get $buffer)) (then {error}))))
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
    if 'arguments' in features:
        parts.append('\n  (func $snek.lib.argv (result (ref $list.str))\n    (local $out (ref $list.str)) (local $ptr i32) (local $n i32) (local $i i32) (local $entry i32)\n    (if (i32.eqz (ref.is_null (global.get $lib.args))) (then (return (ref.as_non_null (global.get $lib.args)))))\n    (call $abi.reset) (call $io.lib-arguments (i32.const 64))\n    (local.set $ptr (i32.load (i32.const 64))) (local.set $n (i32.load (i32.const 68)))\n    (local.set $out (struct.new $list.str (i32.const 0) (array.new_default $arr.str (i32.const 0))))\n    (block $done (loop $copy\n      (br_if $done (i32.ge_u (local.get $i) (local.get $n)))\n      (local.set $entry (i32.add (local.get $ptr) (i32.mul (local.get $i) (i32.const 8))))\n      (call $list.str.append (local.get $out) (call $abi.string (i32.load (local.get $entry)) (i32.load offset=4 (local.get $entry))))\n      (local.set $i (i32.add (local.get $i) (i32.const 1))) (br $copy)))\n    (call $abi.reset) (global.set $lib.args (local.get $out)) (local.get $out))\n')
    if 'environment' in features:
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
    for feature, id_ in (('system-clock', 0), ('monotonic-clock', 1)):
        if feature not in features:
            continue
        if not emitter.component:
            result = '(result i64)' if id_ != 0 else '(param $out i32)'
            finish = '(i64.load (i32.const 80))' if id_ != 0 else '\n    (local.set $n (i64.load (i32.const 80)))\n    (i64.store (local.get $out) (i64.div_u (local.get $n) (i64.const 1000000000)))\n    (i32.store offset=8 (local.get $out) (i32.wrap_i64 (i64.rem_u (local.get $n) (i64.const 1000000000))))'
            parts.append(f"(func $io.lib-{('monotonic' if id_ != 0 else 'system')}-now {result} (local $n i64)\n    (if (call $host.clock_time_get (i32.const {id_}) (i64.const 1) (i32.const 80)) (then {error})) {finish})")
        parts.append('(func $snek.lib.monotonic (result f64)\n    (f64.div (f64.convert_i64_u (call $io.lib-monotonic-now)) (f64.const 1000000000)))' if id_ != 0 else f'\n  (func $snek.lib.time (result f64)\n    (call $io.lib-system-now (i32.const 80))\n    (if (i32.ge_u (i32.load (i32.const 88)) (i32.const 1000000000)) (then {error}))\n    (f64.add (f64.convert_i64_s (i64.load (i32.const 80)))\n      (f64.div (f64.convert_i32_u (i32.load (i32.const 88))) (f64.const 1000000000))))\n')
    if 'sleep' in features:
        negative = emitter.error('ValueError', 'sleep length must be non-negative and not NaN')
        too_large = emitter.error('OverflowError', 'sleep length is too large')
        parts.append(f'\n  (func $lib.duration (param $s f64) (result i64) (local $ns f64)\n    (if (i32.or (f64.ne (local.get $s) (local.get $s)) (f64.lt (local.get $s) (f64.const 0))) (then {negative}))\n    (local.set $ns (f64.ceil (f64.mul (local.get $s) (f64.const 1000000000))))\n    (if (f64.ge (local.get $ns) (f64.const 18446744073709551616)) (then {too_large}))\n    (i64.trunc_f64_u (local.get $ns)))\n')
        if emitter.component:
            parts.append(emit_host_timer_runtime(error))
        else:
            parts.append(f'\n  (func $lib.sleep-core (param $s f64) (local $ns i64)\n    (local.set $ns (call $lib.duration (local.get $s)))\n    (call $rt.flush)\n    (i64.store (i32.const 128) (i64.const 0))\n    (i32.store8 (i32.const 136) (i32.const 0))\n    (i32.store (i32.const 144) (i32.const 1))\n    (i64.store (i32.const 152) (local.get $ns))\n    (i64.store (i32.const 160) (i64.const 1))\n    (i32.store16 (i32.const 168) (i32.const 0))\n    (if (call $host.poll_oneoff (i32.const 128) (i32.const 192) (i32.const 1) (i32.const 240)) (then {error}))\n    (if (i32.or (i32.ne (i32.load (i32.const 240)) (i32.const 1)) (i32.load16_u (i32.const 200))) (then {error})))\n')
    if 'random' in features:
        emitter.types.heap(DeclType('list').add(DeclType('i32')))
        parts.append(emit_host_random_helpers(emitter, error))
    if not emitter.component or 'sleep' not in features:
        parts.append('(func $lib.timer-pump (param i32 i32 i32) (result i32) (i32.const 0))')
    return '\n'.join(parts)

def emit_host_timer_runtime(error: str) -> str:
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

def emit_host_random_helpers(emitter: SourceEmitter, error: str) -> str:
    negative = emitter.error('ValueError', 'number of bytes must be non-negative')
    bound = emitter.error('ValueError', 'upper bound must be positive')
    too_large = emitter.error('OverflowError', 'hex token is too large')
    core = '' if emitter.component else f'\n  (func $io.lib-random (param $n i64) (param $out i32) (local $ptr i32)\n    (if (i64.gt_u (local.get $n) (i64.const 64512)) (then {error}))\n    (local.set $ptr (call $abi.alloc (i32.wrap_i64 (local.get $n)) (i32.const 1)))\n    (if (call $host.random_get (local.get $ptr) (i32.wrap_i64 (local.get $n))) (then {error}))\n    (i32.store (local.get $out) (local.get $ptr)) (i32.store offset=4 (local.get $out) (i32.wrap_i64 (local.get $n))))\n'
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

def emit_io_helpers(emitter: SourceEmitter) -> str:
    read = ''
    if not emitter.component and 'stream_read' in emitter.io:
        read_error = emitter.error('OSError', 'stdin read failed')
        read = f'''
  (func $snek.sys.read-core (param $size i32) (param $line i32) (result (ref $list.i32))
    (local $out (ref $list.i32)) (local $n i32) (local $i i32) (local $want i32) (local $byte i32)
    (call $rt.flush)
    (local.set $out (struct.new $list.i32 (i32.const 0) (array.new_default $arr.i32 (i32.const 0))))
    (block $done (loop $read
      (br_if $done (i32.eqz (local.get $size)))
      (local.set $want (i32.const 64512))
      (if (i32.and (i32.ge_s (local.get $size) (i32.const 0)) (i32.lt_u (local.get $size) (local.get $want)))
        (then (local.set $want (local.get $size))))
      ;; Preview1 has no retained stream buffer. Read lines byte-wise so the
      ;; following call cannot lose bytes after the newline.
      (if (local.get $line) (then (local.set $want (i32.const 1))))
      (i32.store (i32.const 0) (i32.const 1024))
      (i32.store (i32.const 4) (local.get $want))
      (if (call $wasi.fd_read (i32.const 0) (i32.const 0) (i32.const 1) (i32.const 8)) (then {read_error}))
      (local.set $n (i32.load (i32.const 8)))
      (if (i32.gt_u (local.get $n) (local.get $want)) (then {read_error}))
      (br_if $done (i32.eqz (local.get $n)))
      (local.set $i (i32.const 0))
      (loop $copy
        (local.set $byte (i32.load8_u offset=1024 (local.get $i)))
        (call $list.i32.append (local.get $out) (local.get $byte))
        (local.set $i (i32.add (local.get $i) (i32.const 1)))
        (br_if $copy (i32.lt_u (local.get $i) (local.get $n))))
      (if (i32.ge_s (local.get $size) (i32.const 0))
        (then (local.set $size (i32.sub (local.get $size) (local.get $n)))))
      (br_if $done (i32.and (local.get $line) (i32.eq (local.get $byte) (i32.const 10))))
      (br $read))) (local.get $out))
'''
    bad_byte = emitter.error('ValueError', 'byte values must be in range 0..255')
    io_error = emitter.error('OSError', 'stream write failed')
    pipe_error = emitter.error('BrokenPipeError', 'stream closed')
    encoding_error = emitter.error('OSError', 'stream encoding error')
    check = f'\n  (func $snek.sys.check (param $fd i32) (local $error i32)\n    (local.set $error (call $io.take-error (local.get $fd)))\n    (if (i32.eq (local.get $error) (i32.const 3)) (then {pipe_error}))\n    (if (i32.eq (local.get $error) (i32.const 2)) (then {encoding_error}))\n    (if (local.get $error) (then {io_error})))\n' if emitter.component else '(func $snek.sys.check (param $fd i32))'
    closed = emitter.error('ValueError', 'I/O operation on closed stream')
    objects = f"\n  (global $snek.sys.stdin (mut (ref null $io.stream)) (ref.null $io.stream))\n  (global $snek.sys.stdout (mut (ref null $io.stream)) (ref.null $io.stream))\n  (global $snek.sys.stderr (mut (ref null $io.stream)) (ref.null $io.stream))\n  (func $snek.sys.reset\n    (global.set $snek.sys.stdin (ref.null $io.stream))\n    (global.set $snek.sys.stdout (ref.null $io.stream))\n    (global.set $snek.sys.stderr (ref.null $io.stream)))\n  (func $snek.sys.make (param $fd i32) (result (ref $io.stream)) (local $state (ref $io.state))\n    (local.set $state (struct.new $io.state (local.get $fd) (i32.const 0)))\n    (struct.new $io.stream (local.get $state) (struct.new $io.stream (local.get $state) (ref.null $io.stream))))\n  (func $snek.sys.fd (param $stream (ref $io.stream)) (result i32)\n    (struct.get $io.state $fd (struct.get $io.stream $state (local.get $stream))))\n  (func $snek.sys.closed (param $stream (ref $io.stream)) (result i32)\n    (struct.get $io.state $closed (struct.get $io.stream $state (local.get $stream))))\n  (func $snek.sys.ensure_open (param $stream (ref $io.stream))\n    (if (call $snek.sys.closed (local.get $stream)) (then {closed})))\n  (func $snek.sys.buffer (param $stream (ref $io.stream)) (result (ref $io.stream))\n    (ref.as_non_null (struct.get $io.stream $buffer (local.get $stream))))\n  (func $snek.sys.stream_flush (param $stream (ref $io.stream))\n    (call $snek.sys.ensure_open (local.get $stream))\n    (call $snek.sys.flush (call $snek.sys.fd (local.get $stream))))\n  (func $snek.sys.stream_close (param $stream (ref $io.stream)) (local $fd i32)\n    (if (call $snek.sys.closed (local.get $stream)) (then (return)))\n    (local.set $fd (call $snek.sys.fd (local.get $stream)))\n    ;; Closing a view closes its aliases, not the host's underlying descriptor.\n    ;; Mark it closed even when flush reports an error, as a context exit must\n    ;; not leave a resource usable after cleanup has failed.\n    (struct.set $io.state $closed (struct.get $io.stream $state (local.get $stream)) (i32.const 1))\n    (if (local.get $fd)\n      (then (call $snek.sys.flush (local.get $fd)))\n      (else (call $io.input-close))))\n  (func $snek.sys.stream_write (param $stream (ref $io.stream)) (param $text (ref $str)) (result i32)\n    (call $snek.sys.ensure_open (local.get $stream))\n    (call $snek.sys.write (call $snek.sys.fd (local.get $stream)) (local.get $text)))\n  (func $snek.sys.stream_write_bytes (param $stream (ref $io.stream)) (param $data (ref $list.i32)) (result i32)\n    (call $snek.sys.ensure_open (local.get $stream))\n    (call $snek.sys.write_bytes (call $snek.sys.fd (local.get $stream)) (local.get $data)))\n"
    for fd in range(3):
        name = ('stdin', 'stdout', 'stderr')[fd]
        objects += f'\n  (func $snek.sys.get_{name} (result (ref $io.stream))\n    (if (ref.is_null (global.get $snek.sys.{name}))\n      (then (global.set $snek.sys.{name} (call $snek.sys.make (i32.const {fd})))))\n    (ref.as_non_null (global.get $snek.sys.{name})))\n'
    if not emitter.component:
        objects += '(func $io.input-close)'
    return read + objects + check + f'''
  (func $snek.sys.write-all (param $fd i32) (param $ptr i32) (param $n i32)
    (local $written i32)
    (block $done (loop $write
      (br_if $done (i32.eqz (local.get $n)))
      (i32.store (i32.const 0) (local.get $ptr))
      (i32.store (i32.const 4) (local.get $n))
      (if (call $wasi.fd_write (local.get $fd) (i32.const 0) (i32.const 1) (i32.const 8))
        (then {io_error}))
      (local.set $written (i32.load (i32.const 8)))
      (if (i32.or (i32.eqz (local.get $written)) (i32.gt_u (local.get $written) (local.get $n)))
        (then {io_error}))
      (local.set $ptr (i32.add (local.get $ptr) (local.get $written)))
      (local.set $n (i32.sub (local.get $n) (local.get $written))) (br $write))))

  (func $snek.sys.flush (param $fd i32) (local $n i32)
    ;; Preserve ordering with print() before reusing its scratch page.
    (local.set $n (global.get $rt.out_len))
    (global.set $rt.out_len (i32.const 0))
    (call $snek.sys.write-all (i32.const 1) (i32.const 1024) (local.get $n))
    (call $snek.sys.check (local.get $fd)))

  (func $snek.sys.write (param $fd i32) (param $s (ref $str)) (result i32)
    (local $data (ref $u8s)) (local $len i32) (local $i i32) (local $n i32)
    (call $snek.sys.flush (local.get $fd))
    (local.set $data (struct.get $str $bytes (local.get $s)))
    (local.set $len (array.len (local.get $data)))
    (block $done (loop $chunk
      (br_if $done (i32.ge_u (local.get $i) (local.get $len)))
      (local.set $n (i32.const 0))
      (loop $copy
        (i32.store8 offset=1024 (local.get $n) (array.get_u $u8s (local.get $data) (local.get $i)))
        (local.set $i (i32.add (local.get $i) (i32.const 1)))
        (local.set $n (i32.add (local.get $n) (i32.const 1)))
        (br_if $copy (i32.and (i32.lt_u (local.get $i) (local.get $len)) (i32.lt_u (local.get $n) (i32.const 64512)))))
      (call $snek.sys.write-all (local.get $fd) (i32.const 1024) (local.get $n)) (br $chunk)))
    (call $rt.str_len (local.get $s)))

  (func $snek.sys.write_bytes (param $fd i32) (param $values (ref $list.i32)) (result i32)
    (local $data (ref $arr.i32)) (local $len i32) (local $i i32) (local $n i32)
    (local.set $data (struct.get $list.i32 $data (local.get $values)))
    (local.set $len (struct.get $list.i32 $len (local.get $values)))
    ;; Validate the complete list before submitting any bytes from this call.
    (block $checked (loop $check
      (br_if $checked (i32.ge_u (local.get $i) (local.get $len)))
      (if (i32.gt_u (array.get $arr.i32 (local.get $data) (local.get $i)) (i32.const 255)) (then {bad_byte}))
      (local.set $i (i32.add (local.get $i) (i32.const 1))) (br $check)))
    (call $snek.sys.flush (local.get $fd))
    (local.set $i (i32.const 0))
    (block $done (loop $chunk
      (br_if $done (i32.ge_u (local.get $i) (local.get $len)))
      (local.set $n (i32.const 0))
      (loop $copy
        (i32.store8 offset=1024 (local.get $n) (array.get $arr.i32 (local.get $data) (local.get $i)))
        (local.set $i (i32.add (local.get $i) (i32.const 1)))
        (local.set $n (i32.add (local.get $n) (i32.const 1)))
        (br_if $copy (i32.and (i32.lt_u (local.get $i) (local.get $len)) (i32.lt_u (local.get $n) (i32.const 64512)))))
      (call $snek.sys.write-all (local.get $fd) (i32.const 1024) (local.get $n)) (br $chunk)))
    (local.get $len))
'''
