"""UTF-8 and byte-list adapters; GC values stay inside the guest."""
from . import ir
from .types import BOOL, CoroutineT, I32, ListT, NONE, StreamT


def machines(emitter):
    out = []
    stream = ir.LocalGet(StreamT("BinaryOutput"), "stream", StreamT("BinaryOutput"))
    fd = ir.Call(I32, "snek.sys.fd", [stream])
    if "stream_drain" in emitter.stdlib_io:
        body = [ir.ExprStmt(ir.Call(NONE, "snek.sys.stream_flush", [stream]))]
        if emitter.component:
            body += [ir.Yield(None), ir.ExprStmt(ir.Call(NONE, "snek.sys.check", [fd]))]
        out.append(emitter.emit_machine(ir.Func("snek.sys.stream_drain", [("stream", stream.ty)], CoroutineT(NONE), {}, body, machine=True)))
    if "stream_read" in emitter.stdlib_io:
        ty = ListT(I32)
        stream = ir.LocalGet(StreamT("BinaryInput"), "stream", StreamT("BinaryInput"))
        params = [("stream", stream.ty), ("size", I32), ("line", BOOL)]
        args = [ir.LocalGet(t, name, t) for name, t in params[1:]]
        body = [ir.ExprStmt(ir.Call(NONE, "snek.sys.ensure_open", [stream]))]
        if emitter.component:
            body += [ir.ExprStmt(ir.Call(NONE, "io.read-start", args)), ir.Yield(None),
                    ir.Return(ir.Call(ty, "io.read-result", []))]
        else:
            # The compatibility target implements the same coroutine API with
            # blocking preview1 input; it cannot perform host-native async I/O.
            body += [ir.Return(ir.Call(ty, "snek.sys.read-core", args))]
        out.append(emitter.emit_machine(ir.Func("snek.sys.stream_read", params, CoroutineT(ty), {}, body, machine=True)))
    return out


def helpers(emitter):
    read = ""
    if not emitter.component and "stream_read" in emitter.stdlib_io:
        read_error = emitter.raise_code("OSError", "stdin read failed")
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
    bad_byte = emitter.raise_code("ValueError", "byte values must be in range 0..255")
    io_error = emitter.raise_code("OSError", "stream write failed")
    pipe_error = emitter.raise_code("BrokenPipeError", "stream closed")
    encoding_error = emitter.raise_code("OSError", "stream encoding error")
    check = f'''
  (func $snek.sys.check (param $fd i32) (local $error i32)
    (local.set $error (call $io.take-error (local.get $fd)))
    (if (i32.eq (local.get $error) (i32.const 3)) (then {pipe_error}))
    (if (i32.eq (local.get $error) (i32.const 2)) (then {encoding_error}))
    (if (local.get $error) (then {io_error})))
''' if emitter.component else '(func $snek.sys.check (param $fd i32))'
    closed = emitter.raise_code("ValueError", "I/O operation on closed stream")
    objects = f'''
  (global $snek.sys.stdin (mut (ref null $io.stream)) (ref.null $io.stream))
  (global $snek.sys.stdout (mut (ref null $io.stream)) (ref.null $io.stream))
  (global $snek.sys.stderr (mut (ref null $io.stream)) (ref.null $io.stream))
  (func $snek.sys.reset
    (global.set $snek.sys.stdin (ref.null $io.stream))
    (global.set $snek.sys.stdout (ref.null $io.stream))
    (global.set $snek.sys.stderr (ref.null $io.stream)))
  (func $snek.sys.make (param $fd i32) (result (ref $io.stream)) (local $state (ref $io.state))
    (local.set $state (struct.new $io.state (local.get $fd) (i32.const 0)))
    (struct.new $io.stream (local.get $state) (struct.new $io.stream (local.get $state) (ref.null $io.stream))))
  (func $snek.sys.fd (param $stream (ref $io.stream)) (result i32)
    (struct.get $io.state $fd (struct.get $io.stream $state (local.get $stream))))
  (func $snek.sys.closed (param $stream (ref $io.stream)) (result i32)
    (struct.get $io.state $closed (struct.get $io.stream $state (local.get $stream))))
  (func $snek.sys.ensure_open (param $stream (ref $io.stream))
    (if (call $snek.sys.closed (local.get $stream)) (then {closed})))
  (func $snek.sys.buffer (param $stream (ref $io.stream)) (result (ref $io.stream))
    (ref.as_non_null (struct.get $io.stream $buffer (local.get $stream))))
  (func $snek.sys.stream_flush (param $stream (ref $io.stream))
    (call $snek.sys.ensure_open (local.get $stream))
    (call $snek.sys.flush (call $snek.sys.fd (local.get $stream))))
  (func $snek.sys.stream_close (param $stream (ref $io.stream)) (local $fd i32)
    (if (call $snek.sys.closed (local.get $stream)) (then (return)))
    (local.set $fd (call $snek.sys.fd (local.get $stream)))
    ;; Closing a view closes its aliases, not the host's underlying descriptor.
    ;; Mark it closed even when flush reports an error, as a context exit must
    ;; not leave a resource usable after cleanup has failed.
    (struct.set $io.state $closed (struct.get $io.stream $state (local.get $stream)) (i32.const 1))
    (if (local.get $fd)
      (then (call $snek.sys.flush (local.get $fd)))
      (else (call $io.input-close))))
  (func $snek.sys.stream_write (param $stream (ref $io.stream)) (param $text (ref $str)) (result i32)
    (call $snek.sys.ensure_open (local.get $stream))
    (call $snek.sys.write (call $snek.sys.fd (local.get $stream)) (local.get $text)))
  (func $snek.sys.stream_write_bytes (param $stream (ref $io.stream)) (param $data (ref $list.i32)) (result i32)
    (call $snek.sys.ensure_open (local.get $stream))
    (call $snek.sys.write_bytes (call $snek.sys.fd (local.get $stream)) (local.get $data)))
'''
    for fd, name in enumerate(("stdin", "stdout", "stderr")):
        objects += f'''
  (func $snek.sys.get_{name} (result (ref $io.stream))
    (if (ref.is_null (global.get $snek.sys.{name}))
      (then (global.set $snek.sys.{name} (call $snek.sys.make (i32.const {fd})))))
    (ref.as_non_null (global.get $snek.sys.{name})))
'''
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
