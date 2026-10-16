;; Native WASI 0.3 output pump. Synchronous print queues GC-owned chunks;
;; callbacks drain them in order without retaining a Wasm or host stack.
;; [16, 18) is the future<result<_, error-code>> result, [1024, 65536)
;; remains borrowed by a pending stream.write until its completion callback.
(tag $io.exit-tag)
(global $io.head (mut (ref null $io.chunk)) (ref.null $io.chunk))
(global $io.tail (mut (ref null $io.chunk)) (ref.null $io.chunk))
(global $io.root (mut (ref null $coro<none>)) (ref.null $coro<none>))
(global $io.set (mut i32) (i32.const 0))
(global $io.stage (mut i32) (i32.const 0))
(global $io.writer (mut i32) (i32.const 0))
(global $io.future (mut i32) (i32.const 0))
(global $io.offset (mut i32) (i32.const 0))
(global $io.short (mut i32) (i32.const 0))
(global $io.done (mut i32) (i32.const 0))
(global $io.status (mut i32) (i32.const 0))
(global $io.cancelled (mut i32) (i32.const 0))
(global $io.injected (mut i32) (i32.const 0))
(global $io.stdout-error (mut i32) (i32.const 0))
(global $io.stderr-error (mut i32) (i32.const 0))

(func $io.record-error (param $fd i32) (param $error i32)
  (if (i32.eq (local.get $fd) (i32.const 2))
    (then (if (i32.eqz (global.get $io.stderr-error)) (then (global.set $io.stderr-error (local.get $error)))))
    (else (if (i32.eqz (global.get $io.stdout-error)) (then (global.set $io.stdout-error (local.get $error)))))))

(func $io.take-error (param $fd i32) (result i32) (local $error i32)
  (if (i32.eq (local.get $fd) (i32.const 2))
    (then (local.set $error (global.get $io.stderr-error)) (global.set $io.stderr-error (i32.const 0)))
    (else (local.set $error (global.get $io.stdout-error)) (global.set $io.stdout-error (i32.const 0))))
  (local.get $error))

(func $wasi.proc_exit (param $code i32)
  (global.set $io.status (local.get $code))
  (throw $io.exit-tag))

(func $wasi.fd_write (param $fd i32) (param $iov i32) (param $count i32) (param $out i32) (result i32)
  (local $n i32) (local $ptr i32) (local $i i32) (local $bytes (ref $u8s)) (local $chunk (ref $io.chunk))
  ;; The runtime always passes exactly one iovec.
  (if (i32.ne (local.get $count) (i32.const 1)) (then (unreachable)))
  (local.set $ptr (i32.load (local.get $iov)))
  (local.set $n (i32.load offset=4 (local.get $iov)))
  (local.set $bytes (array.new_default $u8s (local.get $n)))
  (block $end (loop $copy
    (br_if $end (i32.ge_u (local.get $i) (local.get $n)))
    (array.set $u8s (local.get $bytes) (local.get $i) (i32.load8_u (i32.add (local.get $ptr) (local.get $i))))
    (local.set $i (i32.add (local.get $i) (i32.const 1))) (br $copy)))
  (local.set $chunk (struct.new $io.chunk (local.get $fd) (local.get $bytes) (ref.null $io.chunk)))
  (if (ref.is_null (global.get $io.tail))
    (then (global.set $io.head (local.get $chunk)))
    (else (struct.set $io.chunk $next (ref.as_non_null (global.get $io.tail)) (local.get $chunk))))
  (global.set $io.tail (local.get $chunk))
  (i32.store (local.get $out) (local.get $n)) (i32.const 0))

(func $io.wait (result i32)
  (i32.or (i32.shl (global.get $io.set) (i32.const 4)) (i32.const 2)))

(func $io.write-result (param $payload i32)
  (global.set $io.offset (i32.add (global.get $io.offset) (i32.shr_u (local.get $payload) (i32.const 4))))
  ;; A dropped reader means the remaining bytes cannot be written.
  (if (i32.and (local.get $payload) (i32.const 15))
    (then
      (if (i32.lt_u (global.get $io.offset) (array.len (struct.get $io.chunk $bytes (ref.as_non_null (global.get $io.head)))))
        (then (global.set $io.short (i32.const 1))))
      (global.set $io.offset (array.len (struct.get $io.chunk $bytes (ref.as_non_null (global.get $io.head))))))))

(func $io.finish-chunk (param $payload i32) (local $error i32)
  ;; Prefer the host's terminal error to a provisional short-write error.
  ;; A dropped future has no result bytes; never interpret stale scratch data.
  (if (local.get $payload)
    (then (local.set $error (i32.const 1)))
    (else (if (i32.load8_u (i32.const 16))
      (then (local.set $error (i32.add (i32.load8_u (i32.const 17)) (i32.const 1))))
      (else (if (global.get $io.short) (then (local.set $error (i32.const 3))))))))
  (call $io.record-error (struct.get $io.chunk $fd (ref.as_non_null (global.get $io.head))) (local.get $error))
  (call $io.join (global.get $io.future) (i32.const 0))
  (call $io.future-drop (global.get $io.future))
  ;; Like preview1's fd_write error path, output failures do not recursively print.
  (global.set $io.head (struct.get $io.chunk $next (ref.as_non_null (global.get $io.head))))
  (if (ref.is_null (global.get $io.head)) (then (global.set $io.tail (ref.null $io.chunk))))
  (global.set $io.stage (i32.const 0)))

(func $io.pump (param $event i32) (param $handle i32) (param $payload i32) (result i32)
  (local $chunk (ref $io.chunk)) (local $bytes (ref $u8s)) (local $i i32) (local $n i32)
  (local $pair i64) (local $result i32) (local $exc (ref $BaseException))
  (local $inject (ref null $BaseException))
  (if (i32.eq (local.get $event) (i32.const 6)) (then (global.set $io.cancelled (i32.const 1))))
  ;; Cancellation does not revoke a stream's borrow of linear memory. Finish
  ;; the in-flight write before running user cleanup or reusing the buffer.
  (if (i32.eq (global.get $io.stage) (i32.const 1))
    (then
      (if (i32.ne (local.get $event) (i32.const 3)) (then (return (call $io.wait))))
      (if (i32.ne (local.get $handle) (global.get $io.writer)) (then (unreachable)))
      (call $io.write-result (local.get $payload)) (global.set $io.stage (i32.const 3))))
  (if (i32.eq (global.get $io.stage) (i32.const 2))
    (then
      (if (i32.ne (local.get $event) (i32.const 4)) (then (return (call $io.wait))))
      (if (i32.ne (local.get $handle) (global.get $io.future)) (then (unreachable)))
      (call $io.finish-chunk (local.get $payload))))
  (loop $pump
    (if (i32.eqz (ref.is_null (global.get $io.head)))
      (then
        (local.set $chunk (ref.as_non_null (global.get $io.head)))
        (local.set $bytes (struct.get $io.chunk $bytes (local.get $chunk)))
        (local.set $n (array.len (local.get $bytes)))
        (if (i32.eqz (global.get $io.stage))
          (then
            (local.set $i (i32.const 0))
            (block $end (loop $copy
              (br_if $end (i32.ge_u (local.get $i) (local.get $n)))
              (i32.store8 offset=1024 (local.get $i) (array.get_u $u8s (local.get $bytes) (local.get $i)))
              (local.set $i (i32.add (local.get $i) (i32.const 1))) (br $copy)))
            (local.set $pair (call $io.stream-new))
            (global.set $io.writer (i32.wrap_i64 (i64.shr_u (local.get $pair) (i64.const 32))))
            (global.set $io.future
              (if (result i32) (i32.eq (struct.get $io.chunk $fd (local.get $chunk)) (i32.const 2))
                (then (call $io.stderr (i32.wrap_i64 (local.get $pair))))
                (else (call $io.stdout (i32.wrap_i64 (local.get $pair))))))
            (call $io.join (global.get $io.writer) (global.get $io.set))
            (global.set $io.offset (i32.const 0)) (global.set $io.short (i32.const 0))
            (global.set $io.stage (i32.const 3))))
        (if (i32.lt_u (global.get $io.offset) (local.get $n))
          (then
            (local.set $result (call $io.stream-write (global.get $io.writer)
              (i32.add (i32.const 1024) (global.get $io.offset)) (i32.sub (local.get $n) (global.get $io.offset))))
            (if (i32.eq (local.get $result) (i32.const -1))
              (then (global.set $io.stage (i32.const 1)) (return (call $io.wait))))
            (call $io.write-result (local.get $result)) (br $pump)))
        (call $io.join (global.get $io.writer) (i32.const 0))
        (call $io.stream-drop (global.get $io.writer))
        (call $io.join (global.get $io.future) (global.get $io.set))
        (local.set $result (call $io.future-read (global.get $io.future) (i32.const 16)))
        (if (i32.eq (local.get $result) (i32.const -1))
          (then (global.set $io.stage (i32.const 2)) (return (call $io.wait))))
        (call $io.finish-chunk (local.get $result)) (br $pump)))
    ;; Input and output serialize ownership of the fixed scratch page. A
    ;; pending read owns it until completion or cancellation is acknowledged.
    (if (call $io.input-pump (local.get $event) (local.get $handle) (local.get $payload))
      (then (return (call $io.wait))))
    (if (call $lib.timer-pump (local.get $event) (local.get $handle) (local.get $payload))
      (then (return (call $io.wait))))
    (if (global.get $io.done)
      (then
        (call $io.input-close)
        (call $io.set-drop (global.get $io.set))
        (global.set $io.root (ref.null $coro<none>))
        (if (global.get $io.status) (then (call $io.exit (global.get $io.status)) (unreachable)))
        (if (global.get $io.cancelled)
          (then (call $io.task-cancel))
          (else (call $io.task-return (i32.const 0))))
        (return (i32.const 0))))
    ;; At a cooperative checkpoint return to the host, even if output completed
    ;; immediately. A fresh callback resumes the frame exactly once.
    (if (i32.eq (local.get $event) (i32.const -1)) (then (return (i32.const 1))))
    (local.set $event (i32.const -1))
    (if (i32.and (global.get $io.cancelled) (i32.eqz (global.get $io.injected)))
      (then (global.set $io.injected (i32.const 1))
        (local.set $inject (call $GeneratorExit.__new__ (ref.null $str)))))
    (block $exit
      (try_table (catch $io.exit-tag $exit)
        (local.set $exc
          (block $caught (result (ref $BaseException))
            (try_table (catch $snek.exc $caught)
              (global.set $io.done (i32.eqz (call $coro<none>.resume
                (ref.as_non_null (global.get $io.root)) (local.get $inject))))
              (local.set $inject (ref.null $BaseException)) (br $exit)) (unreachable)))
        (global.set $io.done (i32.const 1))
        (if (i32.eqz (i32.and (global.get $io.cancelled) (ref.test (ref $GeneratorExit) (local.get $exc))))
          (then (call $rt.uncaught (local.get $exc))))))
    (if (global.get $io.status) (then (global.set $io.done (i32.const 1))))
    (call $rt.flush) (br $pump)) (unreachable))

(func (export "run") (result i32)
  ;; A command instance has one invocation at a time; never overwrite live frames.
  (if (i32.eqz (ref.is_null (global.get $io.root))) (then (unreachable)))
  (global.set $io.done (i32.const 0)) (global.set $io.status (i32.const 0))
  (global.set $io.cancelled (i32.const 0)) (global.set $io.injected (i32.const 0))
  (global.set $io.stdout-error (i32.const 0)) (global.set $io.stderr-error (i32.const 0))
  (call $lib.reset)
  (call $snek.sys.reset)
  (global.set $io.root (call $snek.main))
  (global.set $io.set (call $io.set-new))
  (call $io.pump (i32.const 0) (i32.const 0) (i32.const 0)))

(func (export "callback") (param $event i32) (param $handle i32) (param $payload i32) (result i32)
  (call $io.pump (local.get $event) (local.get $handle) (local.get $payload)))
