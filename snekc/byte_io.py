"""Core WASI byte streams for bootstrap tools; no filesystem authority is added."""


def read_helper(emitter, name="snek.read_bytes", error_class="RuntimeError") -> str:
    read_error = emitter.raise_code(error_class, "stdin read failed")
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


def helpers(emitter) -> str:
    bad_byte = emitter.raise_code("ValueError", "byte values must be in range 0..255")
    write_error = emitter.raise_code("RuntimeError", "stdout write failed")
    return read_helper(emitter) + f'''
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
