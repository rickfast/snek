  ;; ======================================================================
  ;; snek runtime prelude
  ;;
  ;; Spliced into every compiled module by snekc/emit_wat.py. The types it
  ;; uses ($u8s, $str, $buf, ...) and the Unicode table data segments are
  ;; declared by the emitter. Forms written as (str.lit "...") are rewritten by the emitter into
  ;; calls to pooled, lazily-cached string literals.
  ;;
  ;; All language values live on the GC heap. Linear memory exists only for
  ;; the WASI boundary (fd_write wants pointer + length):
  ;;   [0, 8)        iovec { ptr, len }
  ;;   [8, 12)       nwritten out-param
  ;;   [1024, 65536) stdout buffer, also used as stderr scratch
  ;; ======================================================================

  (global $rt.out_len (mut i32) (i32.const 0))

  ;; ---- WASI boundary ---------------------------------------------------

  (func $rt.fd_write_all (param $fd i32) (param $ptr i32) (param $len i32)
    (local $n i32)
    (block $done
      (loop $again
        (br_if $done (i32.le_s (local.get $len) (i32.const 0)))
        (i32.store (i32.const 0) (local.get $ptr))
        (i32.store (i32.const 4) (local.get $len))
        ;; non-zero errno: give up quietly (nothing sensible to report it to)
        (br_if $done (call $wasi.fd_write (local.get $fd) (i32.const 0) (i32.const 1) (i32.const 8)))
        (local.set $n (i32.load (i32.const 8)))
        (br_if $done (i32.eqz (local.get $n)))
        (local.set $ptr (i32.add (local.get $ptr) (local.get $n)))
        (local.set $len (i32.sub (local.get $len) (local.get $n)))
        (br $again))))

  (func $rt.flush
    (call $rt.fd_write_all (i32.const 1) (i32.const 1024) (global.get $rt.out_len))
    (global.set $rt.out_len (i32.const 0)))

  (func $rt.out_byte (param $b i32)
    (if (i32.ge_u (global.get $rt.out_len) (i32.const 64512)) (then (call $rt.flush)))
    (i32.store8 offset=1024 (global.get $rt.out_len) (local.get $b))
    (global.set $rt.out_len (i32.add (global.get $rt.out_len) (i32.const 1))))

  ;; Copy a GC string into the linear-memory stdout buffer. There is no bulk
  ;; "GC array -> linear memory" instruction, so this is a byte loop. Output is
  ;; buffered so fd_write is called once per 63 KiB, not once per print.
  (func $rt.out_str (param $s (ref $str))
    (local $a (ref $u8s)) (local $i i32) (local $n i32)
    (local.set $a (struct.get $str $bytes (local.get $s)))
    (local.set $n (array.len (local.get $a)))
    (block $done
      (loop $l
        (br_if $done (i32.ge_u (local.get $i) (local.get $n)))
        (if (i32.ge_u (global.get $rt.out_len) (i32.const 64512)) (then (call $rt.flush)))
        (i32.store8 offset=1024 (global.get $rt.out_len)
          (array.get_u $u8s (local.get $a) (local.get $i)))
        (global.set $rt.out_len (i32.add (global.get $rt.out_len) (i32.const 1)))
        (local.set $i (i32.add (local.get $i) (i32.const 1)))
        (br $l))))

  ;; Fatal error: flush stdout, write the message to stderr, exit 1. Only used
  ;; for uncaught exceptions; everything recoverable is thrown as an exception.
  (func $rt.panic (param $msg (ref $str))
    (local $a (ref $u8s)) (local $i i32) (local $n i32) (local $k i32)
    (call $rt.flush)
    (local.set $a (struct.get $str $bytes (local.get $msg)))
    (local.set $n (array.len (local.get $a)))
    (block $done
      (loop $l
        (br_if $done (i32.ge_u (local.get $i) (local.get $n)))
        (i32.store8 offset=1024 (local.get $k) (array.get_u $u8s (local.get $a) (local.get $i)))
        (local.set $i (i32.add (local.get $i) (i32.const 1)))
        (local.set $k (i32.add (local.get $k) (i32.const 1)))
        (if (i32.eq (local.get $k) (i32.const 64000))
          (then
            (call $rt.fd_write_all (i32.const 2) (i32.const 1024) (local.get $k))
            (local.set $k (i32.const 0))))
        (br $l)))
    (i32.store8 offset=1024 (local.get $k) (i32.const 10))
    (call $rt.fd_write_all (i32.const 2) (i32.const 1024) (i32.add (local.get $k) (i32.const 1)))
    (call $wasi.proc_exit (i32.const 1))
    (unreachable))

  ;; ---- strings ------------------------------------------------------------
  ;; (struct $bytes $hash $cp $idx)
  ;;   $bytes  UTF-8
  ;;   $hash   FNV-1a, computed lazily (0 = not yet)
  ;;   $cp     number of code points, -1 until counted. It equals the byte
  ;;           length exactly when the string is ASCII, which is the fast path
  ;;           for len(), indexing and slicing.
  ;;   $idx    byte offset of every code point plus the end, built the first
  ;;           time a non-ASCII string is indexed or sliced
  ;; Strings are immutable, so the cached fields never go stale.

  (func $rt.mkstr (param $b (ref $u8s)) (param $cp i32) (result (ref $str))
    (struct.new $str (local.get $b) (i32.const 0) (local.get $cp) (ref.null $u32s)))

  (func $rt.blen (param $s (ref $str)) (result i32)
    (array.len (struct.get $str $bytes (local.get $s))))

  (func $rt.byte (param $s (ref $str)) (param $i i32) (result i32)
    (array.get_u $u8s (struct.get $str $bytes (local.get $s)) (local.get $i)))

  ;; len(s): code points, counted once (every byte that is not 10xxxxxx starts one)
  (func $rt.str_len (param $s (ref $str)) (result i32)
    (local $n i32) (local $a (ref $u8s)) (local $i i32)
    (local.set $n (struct.get $str $cp (local.get $s)))
    (if (i32.ge_s (local.get $n) (i32.const 0)) (then (return (local.get $n))))
    (local.set $a (struct.get $str $bytes (local.get $s)))
    (local.set $n (i32.const 0))
    (block $done
      (loop $l
        (br_if $done (i32.ge_u (local.get $i) (array.len (local.get $a))))
        (local.set $n (i32.add (local.get $n)
          (i32.ne (i32.and (array.get_u $u8s (local.get $a) (local.get $i)) (i32.const 0xC0)) (i32.const 0x80))))
        (local.set $i (i32.add (local.get $i) (i32.const 1)))
        (br $l)))
    (struct.set $str $cp (local.get $s) (local.get $n))
    (local.get $n))

  (func $rt.is_ascii (param $s (ref $str)) (result i32)
    (i32.eq (call $rt.str_len (local.get $s)) (call $rt.blen (local.get $s))))

  ;; code point count of a concatenation: known only if both parts are
  (func $rt.cp_sum (param $a i32) (param $b i32) (result i32)
    (select (i32.add (local.get $a) (local.get $b)) (i32.const -1)
            (i32.and (i32.ge_s (local.get $a) (i32.const 0)) (i32.ge_s (local.get $b) (i32.const 0)))))

  ;; ---- UTF-8 ----

  ;; length of a UTF-8 sequence, from its lead byte
  (func $rt.u8w (param $c i32) (result i32)
    (select (i32.const 1)
      (select (i32.const 2)
        (select (i32.const 3) (i32.const 4) (i32.lt_u (local.get $c) (i32.const 0xF0)))
        (i32.lt_u (local.get $c) (i32.const 0xE0)))
      (i32.lt_u (local.get $c) (i32.const 0x80))))

  ;; length of the UTF-8 encoding of a code point
  (func $rt.cpw (param $cp i32) (result i32)
    (select (i32.const 1)
      (select (i32.const 2)
        (select (i32.const 3) (i32.const 4) (i32.lt_u (local.get $cp) (i32.const 0x10000)))
        (i32.lt_u (local.get $cp) (i32.const 0x800)))
      (i32.lt_u (local.get $cp) (i32.const 0x80))))

  ;; byte length of the code point starting at byte i
  (func $rt.wat (param $s (ref $str)) (param $i i32) (result i32)
    (call $rt.u8w (call $rt.byte (local.get $s) (local.get $i))))

  ;; start of the code point that ends at byte boundary i (i > 0)
  (func $rt.back (param $s (ref $str)) (param $i i32) (result i32)
    (local $a (ref $u8s))
    (local.set $a (struct.get $str $bytes (local.get $s)))
    (loop $l
      (local.set $i (i32.sub (local.get $i) (i32.const 1)))
      (br_if $l (i32.eq (i32.and (array.get_u $u8s (local.get $a) (local.get $i)) (i32.const 0xC0)) (i32.const 0x80))))
    (local.get $i))

  ;; the code point starting at byte i
  (func $rt.dec (param $s (ref $str)) (param $i i32) (result i32)
    (local $a (ref $u8s)) (local $c i32)
    (local.set $a (struct.get $str $bytes (local.get $s)))
    (local.set $c (array.get_u $u8s (local.get $a) (local.get $i)))
    (if (i32.lt_u (local.get $c) (i32.const 0x80)) (then (return (local.get $c))))
    (if (i32.lt_u (local.get $c) (i32.const 0xE0))
      (then (return (i32.or
        (i32.shl (i32.and (local.get $c) (i32.const 0x1F)) (i32.const 6))
        (i32.and (array.get_u $u8s (local.get $a) (i32.add (local.get $i) (i32.const 1))) (i32.const 0x3F))))))
    (if (i32.lt_u (local.get $c) (i32.const 0xF0))
      (then (return (i32.or (i32.or
        (i32.shl (i32.and (local.get $c) (i32.const 0x0F)) (i32.const 12))
        (i32.shl (i32.and (array.get_u $u8s (local.get $a) (i32.add (local.get $i) (i32.const 1))) (i32.const 0x3F))
                 (i32.const 6)))
        (i32.and (array.get_u $u8s (local.get $a) (i32.add (local.get $i) (i32.const 2))) (i32.const 0x3F))))))
    (i32.or (i32.or (i32.or
      (i32.shl (i32.and (local.get $c) (i32.const 0x07)) (i32.const 18))
      (i32.shl (i32.and (array.get_u $u8s (local.get $a) (i32.add (local.get $i) (i32.const 1))) (i32.const 0x3F))
               (i32.const 12)))
      (i32.shl (i32.and (array.get_u $u8s (local.get $a) (i32.add (local.get $i) (i32.const 2))) (i32.const 0x3F))
               (i32.const 6)))
      (i32.and (array.get_u $u8s (local.get $a) (i32.add (local.get $i) (i32.const 3))) (i32.const 0x3F))))

  ;; write cp as UTF-8 at o[p..], return the position after it
  (func $rt.enc (param $o (ref $u8s)) (param $p i32) (param $cp i32) (result i32)
    (if (i32.lt_u (local.get $cp) (i32.const 0x80))
      (then
        (array.set $u8s (local.get $o) (local.get $p) (local.get $cp))
        (return (i32.add (local.get $p) (i32.const 1)))))
    (if (i32.lt_u (local.get $cp) (i32.const 0x800))
      (then
        (array.set $u8s (local.get $o) (local.get $p) (i32.or (i32.const 0xC0) (i32.shr_u (local.get $cp) (i32.const 6))))
        (array.set $u8s (local.get $o) (i32.add (local.get $p) (i32.const 1))
          (i32.or (i32.const 0x80) (i32.and (local.get $cp) (i32.const 0x3F))))
        (return (i32.add (local.get $p) (i32.const 2)))))
    (if (i32.lt_u (local.get $cp) (i32.const 0x10000))
      (then
        (array.set $u8s (local.get $o) (local.get $p) (i32.or (i32.const 0xE0) (i32.shr_u (local.get $cp) (i32.const 12))))
        (array.set $u8s (local.get $o) (i32.add (local.get $p) (i32.const 1))
          (i32.or (i32.const 0x80) (i32.and (i32.shr_u (local.get $cp) (i32.const 6)) (i32.const 0x3F))))
        (array.set $u8s (local.get $o) (i32.add (local.get $p) (i32.const 2))
          (i32.or (i32.const 0x80) (i32.and (local.get $cp) (i32.const 0x3F))))
        (return (i32.add (local.get $p) (i32.const 3)))))
    (array.set $u8s (local.get $o) (local.get $p) (i32.or (i32.const 0xF0) (i32.shr_u (local.get $cp) (i32.const 18))))
    (array.set $u8s (local.get $o) (i32.add (local.get $p) (i32.const 1))
      (i32.or (i32.const 0x80) (i32.and (i32.shr_u (local.get $cp) (i32.const 12)) (i32.const 0x3F))))
    (array.set $u8s (local.get $o) (i32.add (local.get $p) (i32.const 2))
      (i32.or (i32.const 0x80) (i32.and (i32.shr_u (local.get $cp) (i32.const 6)) (i32.const 0x3F))))
    (array.set $u8s (local.get $o) (i32.add (local.get $p) (i32.const 3))
      (i32.or (i32.const 0x80) (i32.and (local.get $cp) (i32.const 0x3F))))
    (i32.add (local.get $p) (i32.const 4)))

  ;; ---- code point indexing ----

  (func $rt.str_index (param $s (ref $str)) (result (ref $u32s))
    (local $x (ref null $u32s)) (local $a (ref $u8s)) (local $n i32) (local $i i32) (local $k i32) (local $t (ref $u32s))
    (local.set $x (struct.get $str $idx (local.get $s)))
    (if (i32.eqz (ref.is_null (local.get $x))) (then (return (ref.as_non_null (local.get $x)))))
    (local.set $n (call $rt.str_len (local.get $s)))
    (local.set $a (struct.get $str $bytes (local.get $s)))
    (local.set $t (array.new_default $u32s (i32.add (local.get $n) (i32.const 1))))
    (block $done
      (loop $l
        (br_if $done (i32.ge_u (local.get $i) (array.len (local.get $a))))
        (if (i32.ne (i32.and (array.get_u $u8s (local.get $a) (local.get $i)) (i32.const 0xC0)) (i32.const 0x80))
          (then
            (array.set $u32s (local.get $t) (local.get $k) (local.get $i))
            (local.set $k (i32.add (local.get $k) (i32.const 1)))))
        (local.set $i (i32.add (local.get $i) (i32.const 1)))
        (br $l)))
    (array.set $u32s (local.get $t) (local.get $n) (array.len (local.get $a)))
    (struct.set $str $idx (local.get $s) (local.get $t))
    (local.get $t))

  ;; byte offset of code point k (0 <= k <= len)
  (func $rt.cpoff (param $s (ref $str)) (param $k i32) (result i32)
    (if (result i32) (call $rt.is_ascii (local.get $s))
      (then (local.get $k))
      (else (array.get $u32s (call $rt.str_index (local.get $s)) (local.get $k)))))

  ;; code point index of a byte boundary
  (func $rt.cpidx (param $s (ref $str)) (param $off i32) (result i32)
    (local $a (ref $u8s)) (local $i i32) (local $n i32)
    (if (call $rt.is_ascii (local.get $s)) (then (return (local.get $off))))
    (local.set $a (struct.get $str $bytes (local.get $s)))
    (block $done
      (loop $l
        (br_if $done (i32.ge_u (local.get $i) (local.get $off)))
        (local.set $n (i32.add (local.get $n)
          (i32.ne (i32.and (array.get_u $u8s (local.get $a) (local.get $i)) (i32.const 0xC0)) (i32.const 0x80))))
        (local.set $i (i32.add (local.get $i) (i32.const 1)))
        (br $l)))
    (local.get $n))

  ;; A one-character string. The 128 ASCII ones are cached, so `for ch in s`
  ;; and s[i] over ASCII text allocate nothing after the first use of a letter.
  (global $rt.chars (mut (ref null $ostrs)) (ref.null $ostrs))

  (func $rt.char_str (param $cp i32) (result (ref $str))
    (local $t (ref $ostrs)) (local $s (ref null $str)) (local $o (ref $u8s))
    (if (i32.lt_u (local.get $cp) (i32.const 128))
      (then
        (if (ref.is_null (global.get $rt.chars))
          (then (global.set $rt.chars (array.new_default $ostrs (i32.const 128)))))
        (local.set $t (ref.as_non_null (global.get $rt.chars)))
        (local.set $s (array.get $ostrs (local.get $t) (local.get $cp)))
        (if (ref.is_null (local.get $s))
          (then
            (local.set $s (call $rt.mkstr (array.new $u8s (local.get $cp) (i32.const 1)) (i32.const 1)))
            (array.set $ostrs (local.get $t) (local.get $cp) (local.get $s))))
        (return (ref.as_non_null (local.get $s)))))
    (local.set $o (array.new_default $u8s (call $rt.cpw (local.get $cp))))
    (drop (call $rt.enc (local.get $o) (i32.const 0) (local.get $cp)))
    (call $rt.mkstr (local.get $o) (i32.const 1)))

  ;; the character starting at byte i (for-loops over a str walk bytes)
  (func $rt.char_at (param $s (ref $str)) (param $i i32) (result (ref $str))
    (call $rt.char_str (call $rt.dec (local.get $s) (local.get $i))))

  ;; s[i]
  (func $rt.str_get (param $s (ref $str)) (param $i i32) (result (ref $str))
    (local $n i32)
    (local.set $n (call $rt.str_len (local.get $s)))
    (if (i32.lt_s (local.get $i) (i32.const 0)) (then (local.set $i (i32.add (local.get $i) (local.get $n)))))
    (if (i32.ge_u (local.get $i) (local.get $n))
      (then (throw $snek.exc (call $IndexError.__new__ (str.lit "string index out of range")))))
    (call $rt.char_at (local.get $s) (call $rt.cpoff (local.get $s) (local.get $i))))

  (func $rt.ord (param $s (ref $str)) (result i32)
    (local $n i32)
    (local.set $n (call $rt.str_len (local.get $s)))
    (if (i32.ne (local.get $n) (i32.const 1))
      (then (throw $snek.exc (call $TypeError.__new__
        (call $rt.str_concat_n (array.new_fixed $strs 3
          (str.lit "ord() expected a character, but string of length ")
          (call $rt.i64_to_str (i64.extend_i32_u (local.get $n)))
          (str.lit " found")))))))
    (call $rt.dec (local.get $s) (i32.const 0)))

  ;; chr(i). A lone surrogate cannot be stored as UTF-8, so chr() of one raises
  ;; where CPython would return a string it could not print.
  (func $rt.chr (param $cp i32) (result (ref $str))
    (if (i32.gt_u (local.get $cp) (i32.const 0x10FFFF))
      (then (throw $snek.exc (call $ValueError.__new__ (str.lit "chr() arg not in range(0x110000)")))))
    (if (i32.eq (i32.and (local.get $cp) (i32.const 0xFFFFF800)) (i32.const 0xD800))
      (then (throw $snek.exc (call $ValueError.__new__
        (str.lit "chr() of a surrogate code point is not supported (snek strings are UTF-8)")))))
    (call $rt.char_str (local.get $cp)))

  ;; ---- slices ----

  (global $rt.slice_start (mut i32) (i32.const 0))

  (func $rt.step_check (param $step i32)
    (if (i32.eqz (local.get $step))
      (then (throw $snek.exc (call $ValueError.__new__ (str.lit "slice step cannot be zero"))))))

  (func $rt.slice_clamp (param $i i32) (param $n i32) (param $step i32) (result i32)
    (if (i32.lt_s (local.get $i) (i32.const 0))
      (then
        (local.set $i (i32.add (local.get $i) (local.get $n)))
        (if (i32.lt_s (local.get $i) (i32.const 0))
          (then (local.set $i (select (i32.const -1) (i32.const 0) (i32.lt_s (local.get $step) (i32.const 0))))))
        (return (local.get $i))))
    (if (i32.ge_s (local.get $i) (local.get $n))
      (then (local.set $i (select (i32.sub (local.get $n) (i32.const 1)) (local.get $n)
                                  (i32.lt_s (local.get $step) (i32.const 0))))))
    (local.get $i))

  ;; slice.indices() for a sequence of length n: returns the slice length and
  ;; leaves the first index in $rt.slice_start. flags bit 0: start was given,
  ;; bit 1: stop was given (absent bounds depend on the sign of step).
  (func $rt.slice_adj (param $n i32) (param $start i32) (param $stop i32) (param $step i32) (param $flags i32)
                      (result i32)
    (if (i32.eq (local.get $step) (i32.const 0x80000000)) (then (local.set $step (i32.const -0x7FFFFFFF))))
    (local.set $start
      (if (result i32) (i32.and (local.get $flags) (i32.const 1))
        (then (call $rt.slice_clamp (local.get $start) (local.get $n) (local.get $step)))
        (else (select (i32.sub (local.get $n) (i32.const 1)) (i32.const 0) (i32.lt_s (local.get $step) (i32.const 0))))))
    (local.set $stop
      (if (result i32) (i32.and (local.get $flags) (i32.const 2))
        (then (call $rt.slice_clamp (local.get $stop) (local.get $n) (local.get $step)))
        (else (select (i32.const -1) (local.get $n) (i32.lt_s (local.get $step) (i32.const 0))))))
    (global.set $rt.slice_start (local.get $start))
    (if (i32.lt_s (local.get $step) (i32.const 0))
      (then
        (if (i32.lt_s (local.get $stop) (local.get $start))
          (then (return (i32.add (i32.div_u (i32.sub (i32.sub (local.get $start) (local.get $stop)) (i32.const 1))
                                            (i32.sub (i32.const 0) (local.get $step)))
                                 (i32.const 1))))))
      (else
        (if (i32.lt_s (local.get $start) (local.get $stop))
          (then (return (i32.add (i32.div_u (i32.sub (i32.sub (local.get $stop) (local.get $start)) (i32.const 1))
                                            (local.get $step))
                                 (i32.const 1)))))))
    (i32.const 0))

  ;; s[start:stop:step]
  (func $rt.str_slice (param $s (ref $str)) (param $start i32) (param $stop i32) (param $step i32) (param $flags i32)
                      (result (ref $str))
    (local $n i32) (local $cnt i32) (local $a i32) (local $b i32) (local $o (ref $u8s)) (local $src (ref $u8s))
    (local $ix (ref $u32s)) (local $k i32) (local $p i32) (local $pos i32) (local $len i32)
    (call $rt.step_check (local.get $step))
    (local.set $n (call $rt.str_len (local.get $s)))
    (local.set $cnt (call $rt.slice_adj (local.get $n) (local.get $start) (local.get $stop) (local.get $step)
                                        (local.get $flags)))
    (local.set $start (global.get $rt.slice_start))
    (if (i32.le_s (local.get $cnt) (i32.const 0)) (then (return (str.lit ""))))
    (local.set $src (struct.get $str $bytes (local.get $s)))
    (if (i32.eq (local.get $step) (i32.const 1))
      (then
        (if (i32.eq (local.get $cnt) (local.get $n)) (then (return (local.get $s))))
        (local.set $a (call $rt.cpoff (local.get $s) (local.get $start)))
        (local.set $b (call $rt.cpoff (local.get $s) (i32.add (local.get $start) (local.get $cnt))))
        (if (i32.eq (local.get $cnt) (i32.const 1)) (then (return (call $rt.char_at (local.get $s) (local.get $a)))))
        (local.set $o (array.new_default $u8s (i32.sub (local.get $b) (local.get $a))))
        (array.copy $u8s $u8s (local.get $o) (i32.const 0) (local.get $src) (local.get $a) (i32.sub (local.get $b) (local.get $a)))
        (return (call $rt.mkstr (local.get $o) (local.get $cnt)))))
    (if (call $rt.is_ascii (local.get $s))
      (then
        (local.set $o (array.new_default $u8s (local.get $cnt)))
        (local.set $p (local.get $start))
        (block $d
          (loop $l
            (br_if $d (i32.ge_s (local.get $k) (local.get $cnt)))
            (array.set $u8s (local.get $o) (local.get $k) (array.get_u $u8s (local.get $src) (local.get $p)))
            (local.set $p (i32.add (local.get $p) (local.get $step)))
            (local.set $k (i32.add (local.get $k) (i32.const 1)))
            (br $l)))
        (return (call $rt.mkstr (local.get $o) (local.get $cnt)))))
    ;; non-ASCII with a step: measure, then copy each code point's bytes
    (local.set $ix (call $rt.str_index (local.get $s)))
    (local.set $p (local.get $start))
    (block $d1
      (loop $l1
        (br_if $d1 (i32.ge_s (local.get $k) (local.get $cnt)))
        (local.set $len (i32.add (local.get $len)
          (i32.sub (array.get $u32s (local.get $ix) (i32.add (local.get $p) (i32.const 1)))
                   (array.get $u32s (local.get $ix) (local.get $p)))))
        (local.set $p (i32.add (local.get $p) (local.get $step)))
        (local.set $k (i32.add (local.get $k) (i32.const 1)))
        (br $l1)))
    (local.set $o (array.new_default $u8s (local.get $len)))
    (local.set $p (local.get $start))
    (local.set $k (i32.const 0))
    (block $d2
      (loop $l2
        (br_if $d2 (i32.ge_s (local.get $k) (local.get $cnt)))
        (local.set $a (array.get $u32s (local.get $ix) (local.get $p)))
        (local.set $b (i32.sub (array.get $u32s (local.get $ix) (i32.add (local.get $p) (i32.const 1))) (local.get $a)))
        (array.copy $u8s $u8s (local.get $o) (local.get $pos) (local.get $src) (local.get $a) (local.get $b))
        (local.set $pos (i32.add (local.get $pos) (local.get $b)))
        (local.set $p (i32.add (local.get $p) (local.get $step)))
        (local.set $k (i32.add (local.get $k) (i32.const 1)))
        (br $l2)))
    (call $rt.mkstr (local.get $o) (local.get $cnt)))

  ;; bytes [a, b) of s, both on code point boundaries
  (func $rt.bsub (param $s (ref $str)) (param $a i32) (param $b i32) (result (ref $str))
    (local $o (ref $u8s)) (local $n i32)
    (local.set $n (i32.sub (local.get $b) (local.get $a)))
    (if (i32.eq (local.get $n) (call $rt.blen (local.get $s))) (then (return (local.get $s))))
    (if (i32.le_s (local.get $n) (i32.const 0)) (then (return (str.lit ""))))
    (if (i32.eq (local.get $n) (call $rt.wat (local.get $s) (local.get $a)))  ;; a single character
      (then (return (call $rt.char_at (local.get $s) (local.get $a)))))
    (local.set $o (array.new_default $u8s (local.get $n)))
    (array.copy $u8s $u8s (local.get $o) (i32.const 0) (struct.get $str $bytes (local.get $s)) (local.get $a) (local.get $n))
    (call $rt.mkstr (local.get $o)
      (select (local.get $n) (i32.const -1)
              (i32.eq (struct.get $str $cp (local.get $s)) (call $rt.blen (local.get $s))))))

  ;; ---- search ----
  ;; UTF-8 is self-synchronizing: a byte-level match of valid UTF-8 always
  ;; starts and ends on code point boundaries, so searching bytes is exact.

  ;; does n occur in h at byte p?
  (func $rt.bmatch (param $h (ref $str)) (param $p i32) (param $n (ref $str)) (result i32)
    (local $a (ref $u8s)) (local $b (ref $u8s)) (local $j i32) (local $m i32)
    (local.set $a (struct.get $str $bytes (local.get $h)))
    (local.set $b (struct.get $str $bytes (local.get $n)))
    (local.set $m (array.len (local.get $b)))
    (if (i32.lt_s (local.get $p) (i32.const 0)) (then (return (i32.const 0))))
    (if (i32.gt_s (i32.add (local.get $p) (local.get $m)) (array.len (local.get $a))) (then (return (i32.const 0))))
    (block $done
      (loop $l
        (br_if $done (i32.ge_u (local.get $j) (local.get $m)))
        (if (i32.ne (array.get_u $u8s (local.get $a) (i32.add (local.get $p) (local.get $j)))
                    (array.get_u $u8s (local.get $b) (local.get $j)))
          (then (return (i32.const 0))))
        (local.set $j (i32.add (local.get $j) (i32.const 1)))
        (br $l)))
    (i32.const 1))

  ;; first byte offset p in [from, to - len(n)] where n occurs in h, or -1
  (func $rt.bfind (param $h (ref $str)) (param $n (ref $str)) (param $from i32) (param $to i32) (result i32)
    (local $a (ref $u8s)) (local $m i32) (local $last i32) (local $first i32)
    (local.set $a (struct.get $str $bytes (local.get $h)))
    (local.set $m (call $rt.blen (local.get $n)))
    (if (i32.eqz (local.get $m))
      (then (return (select (local.get $from) (i32.const -1) (i32.le_s (local.get $from) (local.get $to))))))
    (local.set $last (i32.sub (local.get $to) (local.get $m)))
    (local.set $first (call $rt.byte (local.get $n) (i32.const 0)))
    (block $none
      (loop $scan
        (br_if $none (i32.gt_s (local.get $from) (local.get $last)))
        (if (i32.eq (array.get_u $u8s (local.get $a) (local.get $from)) (local.get $first))
          (then (if (call $rt.bmatch (local.get $h) (local.get $from) (local.get $n))
                  (then (return (local.get $from))))))
        (local.set $from (i32.add (local.get $from) (i32.const 1)))
        (br $scan)))
    (i32.const -1))

  ;; last byte offset p in [from, to - len(n)] where n occurs in h, or -1
  (func $rt.brfind (param $h (ref $str)) (param $n (ref $str)) (param $from i32) (param $to i32) (result i32)
    (local $p i32)
    (if (i32.eqz (call $rt.blen (local.get $n)))
      (then (return (select (local.get $to) (i32.const -1) (i32.le_s (local.get $from) (local.get $to))))))
    (local.set $p (i32.sub (local.get $to) (call $rt.blen (local.get $n))))
    (block $none
      (loop $scan
        (br_if $none (i32.lt_s (local.get $p) (local.get $from)))
        (if (call $rt.bmatch (local.get $h) (local.get $p) (local.get $n)) (then (return (local.get $p))))
        (local.set $p (i32.sub (local.get $p) (i32.const 1)))
        (br $scan)))
    (i32.const -1))

  ;; sub in s
  (func $rt.str_contains (param $s (ref $str)) (param $sub (ref $str)) (result i32)
    (i32.ge_s (call $rt.bfind (local.get $s) (local.get $sub) (i32.const 0) (call $rt.blen (local.get $s))) (i32.const 0)))

  ;; s * k
  (func $rt.str_repeat (param $s (ref $str)) (param $k i32) (result (ref $str))
    (local $a (ref $u8s)) (local $n i32) (local $o (ref $u8s)) (local $i i32) (local $total i64) (local $cp i32)
    (if (i32.le_s (local.get $k) (i32.const 0)) (then (return (str.lit ""))))
    (if (i32.eq (local.get $k) (i32.const 1)) (then (return (local.get $s))))
    (local.set $a (struct.get $str $bytes (local.get $s)))
    (local.set $n (array.len (local.get $a)))
    (if (i32.eqz (local.get $n)) (then (return (local.get $s))))
    (local.set $total (i64.mul (i64.extend_i32_u (local.get $n)) (i64.extend_i32_u (local.get $k))))
    (if (i64.gt_u (local.get $total) (i64.const 0x7FFFFFFF))
      (then (throw $snek.exc (call $OverflowError.__new__ (str.lit "repeated string is too long")))))
    (local.set $o (array.new_default $u8s (i32.wrap_i64 (local.get $total))))
    (block $d
      (loop $l
        (br_if $d (i32.ge_u (local.get $i) (local.get $k)))
        (array.copy $u8s $u8s (local.get $o) (i32.mul (local.get $i) (local.get $n)) (local.get $a) (i32.const 0) (local.get $n))
        (local.set $i (i32.add (local.get $i) (i32.const 1)))
        (br $l)))
    (local.set $cp (struct.get $str $cp (local.get $s)))
    (call $rt.mkstr (local.get $o)
      (select (i32.mul (local.get $cp) (local.get $k)) (i32.const -1) (i32.ge_s (local.get $cp) (i32.const 0)))))

  ;; ---- string builder (used by the str methods in builtins.py) ----

  (func $rt.buf_new (param $cap i32) (result (ref $buf))
    (struct.new $buf
      (array.new_default $u8s (select (local.get $cap) (i32.const 8) (i32.gt_s (local.get $cap) (i32.const 8))))
      (i32.const 0) (i32.const 0)))

  ;; the data array, with room for `extra` more bytes
  (func $rt.buf_room (param $b (ref $buf)) (param $extra i32) (result (ref $u8s))
    (local $d (ref $u8s)) (local $need i32) (local $cap i32) (local $g (ref $u8s))
    (local.set $d (struct.get $buf $b (local.get $b)))
    (local.set $need (i32.add (struct.get $buf $n (local.get $b)) (local.get $extra)))
    (if (i32.le_u (local.get $need) (array.len (local.get $d))) (then (return (local.get $d))))
    (local.set $cap (i32.shl (array.len (local.get $d)) (i32.const 1)))
    (if (i32.lt_u (local.get $cap) (local.get $need)) (then (local.set $cap (local.get $need))))
    (local.set $g (array.new_default $u8s (local.get $cap)))
    (array.copy $u8s $u8s (local.get $g) (i32.const 0) (local.get $d) (i32.const 0) (struct.get $buf $n (local.get $b)))
    (struct.set $buf $b (local.get $b) (local.get $g))
    (local.get $g))

  (func $rt.buf_add_cps (param $b (ref $buf)) (param $k i32)
    (struct.set $buf $cp (local.get $b) (call $rt.cp_sum (struct.get $buf $cp (local.get $b)) (local.get $k))))

  (func $rt.buf_cp (param $b (ref $buf)) (param $cp i32)
    (local $d (ref $u8s))
    (local.set $d (call $rt.buf_room (local.get $b) (i32.const 4)))
    (struct.set $buf $n (local.get $b) (call $rt.enc (local.get $d) (struct.get $buf $n (local.get $b)) (local.get $cp)))
    (call $rt.buf_add_cps (local.get $b) (i32.const 1)))

  ;; append bytes [x, y) of s (on code point boundaries)
  (func $rt.buf_sub (param $b (ref $buf)) (param $s (ref $str)) (param $x i32) (param $y i32)
    (local $d (ref $u8s)) (local $k i32)
    (local.set $k (i32.sub (local.get $y) (local.get $x)))
    (local.set $d (call $rt.buf_room (local.get $b) (local.get $k)))
    (array.copy $u8s $u8s (local.get $d) (struct.get $buf $n (local.get $b))
                (struct.get $str $bytes (local.get $s)) (local.get $x) (local.get $k))
    (struct.set $buf $n (local.get $b) (i32.add (struct.get $buf $n (local.get $b)) (local.get $k)))
    ;; exact for ASCII sources; otherwise the result counts itself if asked
    (call $rt.buf_add_cps (local.get $b)
      (select (local.get $k) (i32.const -1)
              (i32.eq (struct.get $str $cp (local.get $s)) (call $rt.blen (local.get $s))))))

  (func $rt.buf_str (param $b (ref $buf)) (param $s (ref $str))
    (local $d (ref $u8s)) (local $k i32)
    (local.set $k (call $rt.blen (local.get $s)))
    (local.set $d (call $rt.buf_room (local.get $b) (local.get $k)))
    (array.copy $u8s $u8s (local.get $d) (struct.get $buf $n (local.get $b))
                (struct.get $str $bytes (local.get $s)) (i32.const 0) (local.get $k))
    (struct.set $buf $n (local.get $b) (i32.add (struct.get $buf $n (local.get $b)) (local.get $k)))
    (call $rt.buf_add_cps (local.get $b) (struct.get $str $cp (local.get $s))))

  (func $rt.buf_done (param $b (ref $buf)) (result (ref $str))
    (local $d (ref $u8s)) (local $n i32) (local $o (ref $u8s))
    (local.set $d (struct.get $buf $b (local.get $b)))
    (local.set $n (struct.get $buf $n (local.get $b)))
    (if (i32.eq (local.get $n) (array.len (local.get $d)))
      (then (return (call $rt.mkstr (local.get $d) (struct.get $buf $cp (local.get $b))))))
    (local.set $o (array.new_default $u8s (local.get $n)))
    (array.copy $u8s $u8s (local.get $o) (i32.const 0) (local.get $d) (i32.const 0) (local.get $n))
    (call $rt.mkstr (local.get $o) (struct.get $buf $cp (local.get $b))))

  ;; ---- Unicode properties ----
  ;; Tables are generated per program by snekc/unicode.py and cover only the
  ;; code points the program can produce. Flag bits match unicode.py:
  ;; NONPRINT 1, SPACE 2, ALPHA 4, DECIMAL 8, DIGIT 16, NUMERIC 32, UPPER 64,
  ;; LOWER 128, TITLE 256, XID_START 512, XID_CONTINUE 1024, CASE_IGNORABLE 2048.

  (global $rt.ua (mut (ref null $u16s)) (ref.null $u16s))
  (global $rt.uf (mut (ref null $u32s)) (ref.null $u32s))
  (global $rt.uc (mut (ref null $u32s)) (ref.null $u32s))
  (global $rt.usp (mut (ref null $u32s)) (ref.null $u32s))
  (global $rt.uz (mut (ref null $u32s)) (ref.null $u32s))

  (func $rt.flags (param $cp i32) (result i32)
    (if (i32.lt_u (local.get $cp) (i32.const 128))
      (then
        (if (ref.is_null (global.get $rt.ua))
          (then (global.set $rt.ua (array.new_data $u16s $uascii (i32.const 0) (i32.const 128)))))
        (return (array.get_u $u16s (ref.as_non_null (global.get $rt.ua)) (local.get $cp)))))
    (call $rt.flags_hi (local.get $cp)))

  ;; flags of a code point >= 128 (callers that never ask about ASCII use this
  ;; directly, so the ASCII table stays out of their modules)
  (func $rt.flags_hi (param $cp i32) (result i32)
    (local $t (ref $u32s)) (local $lo i32) (local $hi i32) (local $mid i32)
    (if (i32.eqz (global.get $rt.uf.n)) (then (return (i32.const 0))))
    (if (ref.is_null (global.get $rt.uf))
      (then (global.set $rt.uf (array.new_data $u32s $uflags (i32.const 0) (i32.shl (global.get $rt.uf.n) (i32.const 1))))))
    (local.set $t (ref.as_non_null (global.get $rt.uf)))
    ;; the last run starting at or before cp
    (local.set $hi (global.get $rt.uf.n))
    (block $done
      (loop $l
        (br_if $done (i32.ge_u (i32.add (local.get $lo) (i32.const 1)) (local.get $hi)))
        (local.set $mid (i32.shr_u (i32.add (local.get $lo) (local.get $hi)) (i32.const 1)))
        (if (i32.le_u (array.get $u32s (local.get $t) (i32.shl (local.get $mid) (i32.const 1))) (local.get $cp))
          (then (local.set $lo (local.get $mid)))
          (else (local.set $hi (local.get $mid))))
        (br $l)))
    (array.get $u32s (local.get $t) (i32.add (i32.shl (local.get $lo) (i32.const 1)) (i32.const 1))))

  (func $rt.has (param $cp i32) (param $mask i32) (result i32)
    (i32.ne (i32.and (call $rt.flags (local.get $cp)) (local.get $mask)) (i32.const 0)))

  ;; every code point has one of the flags (False for "")
  (func $rt.all_flags (param $s (ref $str)) (param $mask i32) (result i32)
    (local $i i32) (local $n i32) (local $cp i32)
    (local.set $n (call $rt.blen (local.get $s)))
    (if (i32.eqz (local.get $n)) (then (return (i32.const 0))))
    (block $d
      (loop $l
        (br_if $d (i32.ge_u (local.get $i) (local.get $n)))
        (local.set $cp (call $rt.dec (local.get $s) (local.get $i)))
        (if (i32.eqz (call $rt.has (local.get $cp) (local.get $mask))) (then (return (i32.const 0))))
        (local.set $i (i32.add (local.get $i) (call $rt.cpw (local.get $cp))))
        (br $l)))
    (i32.const 1))

  ;; some code point has one of the flags
  (func $rt.any_flags (param $s (ref $str)) (param $mask i32) (result i32)
    (local $i i32) (local $n i32) (local $cp i32)
    (local.set $n (call $rt.blen (local.get $s)))
    (block $d
      (loop $l
        (br_if $d (i32.ge_u (local.get $i) (local.get $n)))
        (local.set $cp (call $rt.dec (local.get $s) (local.get $i)))
        (if (call $rt.has (local.get $cp) (local.get $mask)) (then (return (i32.const 1))))
        (local.set $i (i32.add (local.get $i) (call $rt.cpw (local.get $cp))))
        (br $l)))
    (i32.const 0))

  ;; decimal digit value of cp, or -1 (int() accepts every Unicode decimal digit)
  (func $rt.decimal (param $cp i32) (result i32)
    (local $t (ref $u32s)) (local $lo i32) (local $hi i32) (local $mid i32)
    (if (i32.lt_u (local.get $cp) (i32.const 128))
      (then (return (select (i32.sub (local.get $cp) (i32.const 48)) (i32.const -1)
                            (i32.lt_u (i32.sub (local.get $cp) (i32.const 48)) (i32.const 10))))))
    (if (i32.eqz (i32.and (call $rt.flags_hi (local.get $cp)) (i32.const 8))) (then (return (i32.const -1))))  ;; DECIMAL
    (if (ref.is_null (global.get $rt.uz))
      (then (global.set $rt.uz (array.new_data $u32s $uzeros (i32.const 0) (global.get $rt.uz.n)))))
    (local.set $t (ref.as_non_null (global.get $rt.uz)))
    ;; the last zero digit at or before cp: digits come in runs of ten
    (local.set $hi (array.len (local.get $t)))
    (block $done
      (loop $l
        (br_if $done (i32.ge_u (i32.add (local.get $lo) (i32.const 1)) (local.get $hi)))
        (local.set $mid (i32.shr_u (i32.add (local.get $lo) (local.get $hi)) (i32.const 1)))
        (if (i32.le_u (array.get $u32s (local.get $t) (local.get $mid)) (local.get $cp))
          (then (local.set $lo (local.get $mid)))
          (else (local.set $hi (local.get $mid))))
        (br $l)))
    (i32.sub (local.get $cp) (array.get $u32s (local.get $t) (local.get $lo))))

  ;; Append the full case mapping of cp; kind 0 upper, 1 lower, 2 title,
  ;; 3 casefold. ($ucases starts with the run count of each kind, then the
  ;; runs, 4 words each: start, end, stride, delta. Stride 0 maps to several
  ;; code points: delta is (offset << 2) | count into $uspecial.)
  (func $rt.buf_case (param $b (ref $buf)) (param $cp i32) (param $kind i32)
    (local $t (ref $u32s)) (local $base i32) (local $lo i32) (local $hi i32) (local $mid i32) (local $k i32)
    (local $start i32) (local $stride i32) (local $d i32) (local $sp (ref $u32s))
    (if (i32.lt_u (local.get $cp) (i32.const 128))
      (then
        (if (i32.and (i32.lt_u (i32.sub (local.get $cp) (i32.const 97)) (i32.const 26))
                     (i32.eqz (i32.and (local.get $kind) (i32.const 1))))
          (then (local.set $cp (i32.sub (local.get $cp) (i32.const 32)))))
        (if (i32.and (i32.lt_u (i32.sub (local.get $cp) (i32.const 65)) (i32.const 26))
                     (i32.and (local.get $kind) (i32.const 1)))
          (then (local.set $cp (i32.add (local.get $cp) (i32.const 32)))))
        (call $rt.buf_cp (local.get $b) (local.get $cp))
        (return)))
    (if (ref.is_null (global.get $rt.uc))
      (then (global.set $rt.uc (array.new_data $u32s $ucases (i32.const 0) (global.get $rt.uc.n)))))
    (local.set $t (ref.as_non_null (global.get $rt.uc)))
    (local.set $base (i32.const 4))
    (block $d0
      (loop $l0
        (br_if $d0 (i32.ge_u (local.get $k) (local.get $kind)))
        (local.set $base (i32.add (local.get $base) (i32.shl (array.get $u32s (local.get $t) (local.get $k)) (i32.const 2))))
        (local.set $k (i32.add (local.get $k) (i32.const 1)))
        (br $l0)))
    (local.set $hi (array.get $u32s (local.get $t) (local.get $kind)))
    (if (i32.eqz (local.get $hi)) (then (call $rt.buf_cp (local.get $b) (local.get $cp)) (return)))
    (block $d1
      (loop $l1
        (br_if $d1 (i32.ge_u (i32.add (local.get $lo) (i32.const 1)) (local.get $hi)))
        (local.set $mid (i32.shr_u (i32.add (local.get $lo) (local.get $hi)) (i32.const 1)))
        (if (i32.le_u (array.get $u32s (local.get $t) (i32.add (local.get $base) (i32.shl (local.get $mid) (i32.const 2))))
                      (local.get $cp))
          (then (local.set $lo (local.get $mid)))
          (else (local.set $hi (local.get $mid))))
        (br $l1)))
    (local.set $k (i32.add (local.get $base) (i32.shl (local.get $lo) (i32.const 2))))
    (local.set $start (array.get $u32s (local.get $t) (local.get $k)))
    (if (i32.or (i32.lt_u (local.get $cp) (local.get $start))
                (i32.ge_u (local.get $cp) (array.get $u32s (local.get $t) (i32.add (local.get $k) (i32.const 1)))))
      (then (call $rt.buf_cp (local.get $b) (local.get $cp)) (return)))
    (local.set $stride (array.get $u32s (local.get $t) (i32.add (local.get $k) (i32.const 2))))
    (local.set $d (array.get $u32s (local.get $t) (i32.add (local.get $k) (i32.const 3))))
    (if (i32.eqz (local.get $stride))
      (then
        (if (ref.is_null (global.get $rt.usp))
          (then (global.set $rt.usp (array.new_data $u32s $uspecial (i32.const 0) (global.get $rt.usp.n)))))
        (local.set $sp (ref.as_non_null (global.get $rt.usp)))
        (local.set $k (i32.shr_u (local.get $d) (i32.const 2)))
        (local.set $d (i32.add (local.get $k) (i32.and (local.get $d) (i32.const 3))))
        (block $d2
          (loop $l2
            (br_if $d2 (i32.ge_u (local.get $k) (local.get $d)))
            (call $rt.buf_cp (local.get $b) (array.get $u32s (local.get $sp) (local.get $k)))
            (local.set $k (i32.add (local.get $k) (i32.const 1)))
            (br $l2)))
        (return)))
    (if (i32.eqz (i32.rem_u (i32.sub (local.get $cp) (local.get $start)) (local.get $stride)))
      (then (local.set $cp (i32.add (local.get $cp) (local.get $d)))))
    (call $rt.buf_cp (local.get $b) (local.get $cp)))

  ;; ---- concatenation, comparison, hashing ----

  (func $rt.str_concat (param $x (ref $str)) (param $y (ref $str)) (result (ref $str))
    (local $a (ref $u8s)) (local $b (ref $u8s)) (local $r (ref $u8s)) (local $n i32)
    (local.set $a (struct.get $str $bytes (local.get $x)))
    (local.set $b (struct.get $str $bytes (local.get $y)))
    (local.set $n (array.len (local.get $a)))
    (local.set $r (array.new_default $u8s (i32.add (local.get $n) (array.len (local.get $b)))))
    (array.copy $u8s $u8s (local.get $r) (i32.const 0) (local.get $a) (i32.const 0) (local.get $n))
    (array.copy $u8s $u8s (local.get $r) (local.get $n) (local.get $b) (i32.const 0) (array.len (local.get $b)))
    (call $rt.mkstr (local.get $r) (call $rt.cp_sum (struct.get $str $cp (local.get $x)) (struct.get $str $cp (local.get $y)))))

  ;; a + b + c ... and f-strings: one exact-size allocation and one copy per
  ;; part, instead of re-copying the growing prefix at every `+`.
  (func $rt.str_concat_n (param $parts (ref $strs)) (result (ref $str))
    (local $i i32) (local $n i32) (local $len i32) (local $pos i32) (local $cp i32)
    (local $r (ref $u8s)) (local $a (ref $u8s)) (local $p (ref $str))
    (local.set $n (array.len (local.get $parts)))
    (block $d1
      (loop $l1
        (br_if $d1 (i32.ge_u (local.get $i) (local.get $n)))
        (local.set $p (array.get $strs (local.get $parts) (local.get $i)))
        (local.set $len (i32.add (local.get $len) (call $rt.blen (local.get $p))))
        (local.set $cp (call $rt.cp_sum (local.get $cp) (struct.get $str $cp (local.get $p))))
        (local.set $i (i32.add (local.get $i) (i32.const 1)))
        (br $l1)))
    (local.set $r (array.new_default $u8s (local.get $len)))
    (local.set $i (i32.const 0))
    (block $d2
      (loop $l2
        (br_if $d2 (i32.ge_u (local.get $i) (local.get $n)))
        (local.set $a (struct.get $str $bytes (array.get $strs (local.get $parts) (local.get $i))))
        (array.copy $u8s $u8s (local.get $r) (local.get $pos) (local.get $a) (i32.const 0) (array.len (local.get $a)))
        (local.set $pos (i32.add (local.get $pos) (array.len (local.get $a))))
        (local.set $i (i32.add (local.get $i) (i32.const 1)))
        (br $l2)))
    (call $rt.mkstr (local.get $r) (local.get $cp)))

  (func $rt.str_eq (param $x (ref $str)) (param $y (ref $str)) (result i32)
    (local $a (ref $u8s)) (local $b (ref $u8s)) (local $i i32) (local $n i32)
    (if (ref.eq (local.get $x) (local.get $y)) (then (return (i32.const 1))))
    (local.set $a (struct.get $str $bytes (local.get $x)))
    (local.set $b (struct.get $str $bytes (local.get $y)))
    (local.set $n (array.len (local.get $a)))
    (if (i32.ne (local.get $n) (array.len (local.get $b))) (then (return (i32.const 0))))
    (block $done
      (loop $l
        (br_if $done (i32.ge_u (local.get $i) (local.get $n)))
        (if (i32.ne (array.get_u $u8s (local.get $a) (local.get $i))
                    (array.get_u $u8s (local.get $b) (local.get $i)))
          (then (return (i32.const 0))))
        (local.set $i (i32.add (local.get $i) (i32.const 1)))
        (br $l)))
    (i32.const 1))

  (func $rt.opt_str_eq (param $x (ref null $str)) (param $y (ref null $str)) (result i32)
    (if (ref.is_null (local.get $x)) (then (return (ref.is_null (local.get $y)))))
    (if (ref.is_null (local.get $y)) (then (return (i32.const 0))))
    (call $rt.str_eq (ref.as_non_null (local.get $x)) (ref.as_non_null (local.get $y))))

  ;; Bytewise comparison. For valid UTF-8 this is code point order, which is
  ;; also what CPython uses for str < str.
  (func $rt.str_cmp (param $x (ref $str)) (param $y (ref $str)) (result i32)
    (local $a (ref $u8s)) (local $b (ref $u8s)) (local $i i32) (local $n i32)
    (local $ca i32) (local $cb i32)
    (local.set $a (struct.get $str $bytes (local.get $x)))
    (local.set $b (struct.get $str $bytes (local.get $y)))
    (local.set $n (select (array.len (local.get $a)) (array.len (local.get $b))
                          (i32.lt_u (array.len (local.get $a)) (array.len (local.get $b)))))
    (block $done
      (loop $l
        (br_if $done (i32.ge_u (local.get $i) (local.get $n)))
        (local.set $ca (array.get_u $u8s (local.get $a) (local.get $i)))
        (local.set $cb (array.get_u $u8s (local.get $b) (local.get $i)))
        (if (i32.ne (local.get $ca) (local.get $cb))
          (then (return (select (i32.const -1) (i32.const 1) (i32.lt_u (local.get $ca) (local.get $cb))))))
        (local.set $i (i32.add (local.get $i) (i32.const 1)))
        (br $l)))
    (i32.sub (i32.gt_u (array.len (local.get $a)) (array.len (local.get $b)))
             (i32.lt_u (array.len (local.get $a)) (array.len (local.get $b)))))

  ;; FNV-1a, computed lazily and cached in the string header (0 = not yet
  ;; computed). Not used by the language yet; dicts will key off it.
  (func $rt.str_hash (param $s (ref $str)) (result i32)
    (local $h i32) (local $a (ref $u8s)) (local $i i32) (local $n i32)
    (local.set $h (struct.get $str $hash (local.get $s)))
    (if (local.get $h) (then (return (local.get $h))))
    (local.set $a (struct.get $str $bytes (local.get $s)))
    (local.set $n (array.len (local.get $a)))
    (local.set $h (i32.const 0x811c9dc5))
    (block $done
      (loop $l
        (br_if $done (i32.ge_u (local.get $i) (local.get $n)))
        (local.set $h (i32.mul (i32.xor (local.get $h) (array.get_u $u8s (local.get $a) (local.get $i)))
                               (i32.const 16777619)))
        (local.set $i (i32.add (local.get $i) (i32.const 1)))
        (br $l)))
    (if (i32.eqz (local.get $h)) (then (local.set $h (i32.const 1))))
    (struct.set $str $hash (local.get $s) (local.get $h))
    (local.get $h))

  ;; ---- number -> str ------------------------------------------------------

  (func $rt.bool_to_str (param $b i32) (result (ref $str))
    (if (result (ref $str)) (local.get $b)
      (then (str.lit "True"))
      (else (str.lit "False"))))

  ;; Count the digits first so the bytes are allocated once, at their final size.
  (func $rt.i64_to_str (param $v i64) (result (ref $str))
    (local $out (ref $u8s)) (local $n i32) (local $u i64) (local $t i64) (local $neg i32)
    (local.set $neg (i64.lt_s (local.get $v) (i64.const 0)))
    ;; 0 - INT64_MIN wraps to itself, which is 2^63 read as unsigned: correct.
    (local.set $u (select (i64.sub (i64.const 0) (local.get $v)) (local.get $v) (local.get $neg)))
    (local.set $t (local.get $u))
    (local.set $n (local.get $neg))
    (loop $count
      (local.set $n (i32.add (local.get $n) (i32.const 1)))
      (local.set $t (i64.div_u (local.get $t) (i64.const 10)))
      (br_if $count (i64.ne (local.get $t) (i64.const 0))))
    (local.set $out (array.new_default $u8s (local.get $n)))
    (loop $l
      (local.set $n (i32.sub (local.get $n) (i32.const 1)))
      (array.set $u8s (local.get $out) (local.get $n)
        (i32.add (i32.const 48) (i32.wrap_i64 (i64.rem_u (local.get $u) (i64.const 10)))))
      (local.set $u (i64.div_u (local.get $u) (i64.const 10)))
      (br_if $l (i64.ne (local.get $u) (i64.const 0))))
    (if (local.get $neg) (then (array.set $u8s (local.get $out) (i32.const 0) (i32.const 45))))
    (call $rt.mkstr (local.get $out) (array.len (local.get $out))))

  (func $rt.pow10 (param $n i32) (result f64)
    (local $r f64)
    (local.set $r (f64.const 1))
    (block $done
      (loop $l
        (br_if $done (i32.le_s (local.get $n) (i32.const 0)))
        (local.set $r (f64.mul (local.get $r) (f64.const 10)))
        (local.set $n (i32.sub (local.get $n) (i32.const 1)))
        (br $l)))
    (local.get $r))

  ;; a * 10^k for any k, in steps that keep the power of ten finite.
  (func $rt.scale (param $a f64) (param $k i32) (result f64)
    (block $d1
      (loop $l1
        (br_if $d1 (i32.le_s (local.get $k) (i32.const 300)))
        (local.set $a (f64.mul (local.get $a) (f64.const 1e300)))
        (local.set $k (i32.sub (local.get $k) (i32.const 300)))
        (br $l1)))
    (block $d2
      (loop $l2
        (br_if $d2 (i32.ge_s (local.get $k) (i32.const -300)))
        (local.set $a (f64.div (local.get $a) (f64.const 1e300)))
        (local.set $k (i32.add (local.get $k) (i32.const 300)))
        (br $l2)))
    (if (result f64) (i32.ge_s (local.get $k) (i32.const 0))
      (then (f64.mul (local.get $a) (call $rt.pow10 (local.get $k))))
      (else (f64.div (local.get $a) (call $rt.pow10 (i32.sub (i32.const 0) (local.get $k)))))))

  (func $rt.put (param $o (ref $u8s)) (param $pos i32) (param $b i32) (result i32)
    (array.set $u8s (local.get $o) (local.get $pos) (local.get $b))
    (i32.add (local.get $pos) (i32.const 1)))

  ;; Approximates CPython's float repr: the shortest of 15/16/17 significant
  ;; digits that round-trips, laid out with repr's fixed/scientific switch
  ;; (scientific when the exponent is < -4 or >= 16). Exact for the common
  ;; cases; a real Ryu/Grisu port is on the roadmap.
  (func $rt.f64_to_str (param $v f64) (result (ref $str))
    (local $neg i32) (local $a f64) (local $e i32) (local $t f64) (local $p i32) (local $k i32)
    (local $d i64) (local $dg (ref $u8s)) (local $pos i32) (local $nd i32) (local $nz i32)
    (local $ed i32) (local $o (ref $u8s)) (local $op i32) (local $i i32) (local $ae i32)
    (local $r (ref $u8s))
    (if (f64.ne (local.get $v) (local.get $v)) (then (return (str.lit "nan"))))
    (local.set $neg (i64.lt_s (i64.reinterpret_f64 (local.get $v)) (i64.const 0)))
    (local.set $a (f64.abs (local.get $v)))
    (if (f64.eq (local.get $a) (f64.const inf))
      (then (return (if (result (ref $str)) (local.get $neg)
                      (then (str.lit "-inf")) (else (str.lit "inf"))))))
    (if (f64.eq (local.get $a) (f64.const 0))
      (then (return (if (result (ref $str)) (local.get $neg)
                      (then (str.lit "-0.0")) (else (str.lit "0.0"))))))
    ;; e = floor(log10(a))
    (if (f64.ge (local.get $a) (f64.const 1))
      (then
        (local.set $t (f64.const 10))
        (block $d1
          (loop $l1
            (br_if $d1 (f64.lt (local.get $a) (local.get $t)))
            (local.set $t (f64.mul (local.get $t) (f64.const 10)))
            (local.set $e (i32.add (local.get $e) (i32.const 1)))
            (br $l1))))
      (else
        (local.set $t (f64.const 1))
        (block $d2
          (loop $l2
            (br_if $d2 (f64.le (local.get $t) (local.get $a)))
            (local.set $t (f64.div (local.get $t) (f64.const 10)))
            (local.set $e (i32.sub (local.get $e) (i32.const 1)))
            (br $l2)))))
    ;; shortest digit count (15..17) whose decimal reads back as the same double
    (local.set $p (i32.const 15))
    (block $found
      (loop $try
        (local.set $k (i32.sub (i32.sub (local.get $p) (i32.const 1)) (local.get $e)))
        (local.set $d (i64.trunc_sat_f64_u (f64.nearest (call $rt.scale (local.get $a) (local.get $k)))))
        (br_if $found (i32.ge_s (local.get $p) (i32.const 17)))
        (br_if $found (f64.eq (call $rt.scale (f64.convert_i64_u (local.get $d))
                                              (i32.sub (i32.const 0) (local.get $k)))
                              (local.get $a)))
        (local.set $p (i32.add (local.get $p) (i32.const 1)))
        (br $try)))
    ;; digits of d, written right-aligned into dg[pos, 24)
    (local.set $dg (array.new_default $u8s (i32.const 24)))
    (local.set $pos (i32.const 24))
    (loop $dl
      (local.set $pos (i32.sub (local.get $pos) (i32.const 1)))
      (array.set $u8s (local.get $dg) (local.get $pos)
        (i32.add (i32.const 48) (i32.wrap_i64 (i64.rem_u (local.get $d) (i64.const 10)))))
      (local.set $d (i64.div_u (local.get $d) (i64.const 10)))
      (br_if $dl (i64.ne (local.get $d) (i64.const 0))))
    (local.set $nd (i32.sub (i32.const 24) (local.get $pos)))
    ;; decimal exponent of the leading digit
    (local.set $ed (i32.sub (i32.sub (local.get $nd) (i32.const 1)) (local.get $k)))
    ;; strip trailing zeros (keep at least one digit)
    (local.set $nz (local.get $nd))
    (block $sd
      (loop $sl
        (br_if $sd (i32.le_s (local.get $nz) (i32.const 1)))
        (br_if $sd (i32.ne (array.get_u $u8s (local.get $dg)
                             (i32.add (local.get $pos) (i32.sub (local.get $nz) (i32.const 1))))
                           (i32.const 48)))
        (local.set $nz (i32.sub (local.get $nz) (i32.const 1)))
        (br $sl)))
    (local.set $o (array.new_default $u8s (i32.const 40)))
    (if (local.get $neg) (then (local.set $op (call $rt.put (local.get $o) (local.get $op) (i32.const 45)))))
    (if (i32.and (i32.ge_s (local.get $ed) (i32.const -4)) (i32.lt_s (local.get $ed) (i32.const 16)))
      (then
        (if (i32.ge_s (local.get $ed) (i32.const 0))
          (then
            ;; integer part: digits 0..ed, padded with zeros past the last significant digit
            (local.set $i (i32.const 0))
            (block $id
              (loop $il
                (br_if $id (i32.gt_s (local.get $i) (local.get $ed)))
                (local.set $op (call $rt.put (local.get $o) (local.get $op)
                  (if (result i32) (i32.lt_s (local.get $i) (local.get $nz))
                    (then (array.get_u $u8s (local.get $dg) (i32.add (local.get $pos) (local.get $i))))
                    (else (i32.const 48)))))
                (local.set $i (i32.add (local.get $i) (i32.const 1)))
                (br $il)))
            (local.set $op (call $rt.put (local.get $o) (local.get $op) (i32.const 46)))
            (if (i32.gt_s (local.get $nz) (i32.add (local.get $ed) (i32.const 1)))
              (then
                (local.set $i (i32.add (local.get $ed) (i32.const 1)))
                (block $fd
                  (loop $fl
                    (br_if $fd (i32.ge_s (local.get $i) (local.get $nz)))
                    (local.set $op (call $rt.put (local.get $o) (local.get $op)
                      (array.get_u $u8s (local.get $dg) (i32.add (local.get $pos) (local.get $i)))))
                    (local.set $i (i32.add (local.get $i) (i32.const 1)))
                    (br $fl))))
              (else (local.set $op (call $rt.put (local.get $o) (local.get $op) (i32.const 48))))))
          (else
            ;; 0.000ddd
            (local.set $op (call $rt.put (local.get $o) (local.get $op) (i32.const 48)))
            (local.set $op (call $rt.put (local.get $o) (local.get $op) (i32.const 46)))
            (local.set $i (i32.const -1))
            (block $zd
              (loop $zl
                (br_if $zd (i32.le_s (local.get $i) (local.get $ed)))
                (local.set $op (call $rt.put (local.get $o) (local.get $op) (i32.const 48)))
                (local.set $i (i32.sub (local.get $i) (i32.const 1)))
                (br $zl)))
            (local.set $i (i32.const 0))
            (block $gd
              (loop $gl
                (br_if $gd (i32.ge_s (local.get $i) (local.get $nz)))
                (local.set $op (call $rt.put (local.get $o) (local.get $op)
                  (array.get_u $u8s (local.get $dg) (i32.add (local.get $pos) (local.get $i)))))
                (local.set $i (i32.add (local.get $i) (i32.const 1)))
                (br $gl))))))
      (else
        ;; d.ddde+XX
        (local.set $op (call $rt.put (local.get $o) (local.get $op)
          (array.get_u $u8s (local.get $dg) (local.get $pos))))
        (if (i32.gt_s (local.get $nz) (i32.const 1))
          (then
            (local.set $op (call $rt.put (local.get $o) (local.get $op) (i32.const 46)))
            (local.set $i (i32.const 1))
            (block $md
              (loop $ml
                (br_if $md (i32.ge_s (local.get $i) (local.get $nz)))
                (local.set $op (call $rt.put (local.get $o) (local.get $op)
                  (array.get_u $u8s (local.get $dg) (i32.add (local.get $pos) (local.get $i)))))
                (local.set $i (i32.add (local.get $i) (i32.const 1)))
                (br $ml)))))
        (local.set $op (call $rt.put (local.get $o) (local.get $op) (i32.const 101)))
        (local.set $op (call $rt.put (local.get $o) (local.get $op)
          (select (i32.const 45) (i32.const 43) (i32.lt_s (local.get $ed) (i32.const 0)))))
        (local.set $ae (select (i32.sub (i32.const 0) (local.get $ed)) (local.get $ed)
                               (i32.lt_s (local.get $ed) (i32.const 0))))
        (if (i32.ge_s (local.get $ae) (i32.const 100))
          (then (local.set $op (call $rt.put (local.get $o) (local.get $op)
                  (i32.add (i32.const 48) (i32.div_u (local.get $ae) (i32.const 100)))))))
        (local.set $op (call $rt.put (local.get $o) (local.get $op)
          (i32.add (i32.const 48) (i32.rem_u (i32.div_u (local.get $ae) (i32.const 10)) (i32.const 10)))))
        (local.set $op (call $rt.put (local.get $o) (local.get $op)
          (i32.add (i32.const 48) (i32.rem_u (local.get $ae) (i32.const 10)))))))
    (local.set $r (array.new_default $u8s (local.get $op)))
    (array.copy $u8s $u8s (local.get $r) (i32.const 0) (local.get $o) (i32.const 0) (local.get $op))
    (call $rt.mkstr (local.get $r) (local.get $op)))

  ;; ---- Python division semantics ---------------------------------------
  ;; // floors and % takes the sign of the divisor; Wasm's div_s/rem_s
  ;; truncate and take the sign of the dividend. Division by zero is a Wasm
  ;; trap (uncatchable), so it is checked here and raised as a real
  ;; ZeroDivisionError, with CPython's messages.

  (func $rt.zerodiv (param $msg (ref $str))
    (throw $snek.exc (call $ZeroDivisionError.__new__ (local.get $msg))))

  (func $rt.i32.floordiv (param $a i32) (param $b i32) (result i32)
    (local $q i32) (local $r i32)
    (if (i32.eqz (local.get $b)) (then (call $rt.zerodiv (str.lit "integer division or modulo by zero"))))
    ;; INT_MIN // -1 traps in Wasm; fixed-width semantics wrap instead
    (if (i32.eq (local.get $b) (i32.const -1)) (then (return (i32.sub (i32.const 0) (local.get $a)))))
    (local.set $q (i32.div_s (local.get $a) (local.get $b)))
    (local.set $r (i32.rem_s (local.get $a) (local.get $b)))
    (if (result i32) (i32.and (i32.ne (local.get $r) (i32.const 0))
                              (i32.lt_s (i32.xor (local.get $r) (local.get $b)) (i32.const 0)))
      (then (i32.sub (local.get $q) (i32.const 1)))
      (else (local.get $q))))

  (func $rt.i32.mod (param $a i32) (param $b i32) (result i32)
    (local $r i32)
    (if (i32.eqz (local.get $b)) (then (call $rt.zerodiv (str.lit "integer modulo by zero"))))
    (local.set $r (i32.rem_s (local.get $a) (local.get $b)))
    (if (result i32) (i32.and (i32.ne (local.get $r) (i32.const 0))
                              (i32.lt_s (i32.xor (local.get $r) (local.get $b)) (i32.const 0)))
      (then (i32.add (local.get $r) (local.get $b)))
      (else (local.get $r))))

  (func $rt.i64.floordiv (param $a i64) (param $b i64) (result i64)
    (local $q i64) (local $r i64)
    (if (i64.eqz (local.get $b)) (then (call $rt.zerodiv (str.lit "integer division or modulo by zero"))))
    (if (i64.eq (local.get $b) (i64.const -1)) (then (return (i64.sub (i64.const 0) (local.get $a)))))
    (local.set $q (i64.div_s (local.get $a) (local.get $b)))
    (local.set $r (i64.rem_s (local.get $a) (local.get $b)))
    (if (result i64) (i32.and (i64.ne (local.get $r) (i64.const 0))
                              (i64.lt_s (i64.xor (local.get $r) (local.get $b)) (i64.const 0)))
      (then (i64.sub (local.get $q) (i64.const 1)))
      (else (local.get $q))))

  (func $rt.i64.mod (param $a i64) (param $b i64) (result i64)
    (local $r i64)
    (if (i64.eqz (local.get $b)) (then (call $rt.zerodiv (str.lit "integer modulo by zero"))))
    (local.set $r (i64.rem_s (local.get $a) (local.get $b)))
    (if (result i64) (i32.and (i64.ne (local.get $r) (i64.const 0))
                              (i64.lt_s (i64.xor (local.get $r) (local.get $b)) (i64.const 0)))
      (then (i64.add (local.get $r) (local.get $b)))
      (else (local.get $r))))

  ;; i32 / i32: computed in f64 (exact), but CPython's message is the int one
  (func $rt.f64.div_int (param $a f64) (param $b f64) (result f64)
    (if (f64.eq (local.get $b) (f64.const 0)) (then (call $rt.zerodiv (str.lit "division by zero"))))
    (f64.div (local.get $a) (local.get $b)))

  (func $rt.f64.div (param $a f64) (param $b f64) (result f64)
    (if (f64.eq (local.get $b) (f64.const 0)) (then (call $rt.zerodiv (str.lit "float division by zero"))))
    (f64.div (local.get $a) (local.get $b)))

  (func $rt.f64.floordiv (param $a f64) (param $b f64) (result f64)
    (if (f64.eq (local.get $b) (f64.const 0))
      (then (call $rt.zerodiv (str.lit "float floor division by zero"))))
    (f64.floor (f64.div (local.get $a) (local.get $b))))

  (func $rt.f64.mod (param $a f64) (param $b f64) (result f64)
    (local $r f64)
    (if (f64.eq (local.get $b) (f64.const 0)) (then (call $rt.zerodiv (str.lit "float modulo by zero"))))
    ;; Wasm has no fmod; a - b*trunc(a/b) is exact for moderate magnitudes
    (local.set $r (f64.sub (local.get $a)
                           (f64.mul (local.get $b) (f64.trunc (f64.div (local.get $a) (local.get $b))))))
    (if (i32.and (f64.ne (local.get $r) (f64.const 0))
                 (i32.ne (f64.lt (local.get $r) (f64.const 0)) (f64.lt (local.get $b) (f64.const 0))))
      (then (local.set $r (f64.add (local.get $r) (local.get $b)))))
    (if (f64.eq (local.get $r) (f64.const 0))
      (then (local.set $r (f64.copysign (f64.const 0) (local.get $b)))))
    (local.get $r))

  ;; ---- float -> int ---------------------------------------------------------
  ;; i32(x) / i64(x) on a float truncate toward zero like int(x). NaN and
  ;; infinity raise like CPython; values that do not fit the target width raise
  ;; OverflowError (CPython would return a big int, which snek cannot represent).

  (func $rt.check_float_int (param $a f64)
    (if (f64.ne (local.get $a) (local.get $a))
      (then (throw $snek.exc (call $ValueError.__new__ (str.lit "cannot convert float NaN to integer")))))
    (if (f64.eq (f64.abs (local.get $a)) (f64.const inf))
      (then (throw $snek.exc (call $OverflowError.__new__ (str.lit "cannot convert float infinity to integer"))))))

  (func $rt.f64_to_i32 (param $a f64) (result i32)
    (local $t f64)
    (call $rt.check_float_int (local.get $a))
    (local.set $t (f64.trunc (local.get $a)))
    (if (i32.or (f64.lt (local.get $t) (f64.const -2147483648)) (f64.gt (local.get $t) (f64.const 2147483647)))
      (then (throw $snek.exc (call $OverflowError.__new__ (str.lit "float too large to convert to i32")))))
    (i32.trunc_f64_s (local.get $t)))

  (func $rt.f64_to_i64 (param $a f64) (result i64)
    (local $t f64)
    (call $rt.check_float_int (local.get $a))
    (local.set $t (f64.trunc (local.get $a)))
    (if (i32.or (f64.lt (local.get $t) (f64.const -9223372036854775808))
                (f64.ge (local.get $t) (f64.const 9223372036854775808)))
      (then (throw $snek.exc (call $OverflowError.__new__ (str.lit "float too large to convert to i64")))))
    (i64.trunc_f64_s (local.get $t)))

  ;; ---- repr(str) --------------------------------------------------------------
  ;; CPython's quoting: single quotes unless the text contains ' and no ", with
  ;; \\ \n \r \t escaped, other ASCII controls as \xNN, and every non-printable
  ;; code point (str.isprintable() is False) as \xNN, \uNNNN or \UNNNNNNNN.
  ;; Printability comes from the per-program Unicode table (NONPRINT flag).

  (func $rt.hexdigit (param $d i32) (result i32)
    (i32.add (local.get $d) (select (i32.const 87) (i32.const 48) (i32.ge_u (local.get $d) (i32.const 10)))))

  ;; backslash, a letter, then `digits` lowercase hex digits of v
  (func $rt.put_escape (param $o (ref $u8s)) (param $pos i32) (param $letter i32) (param $v i32)
                       (param $digits i32) (result i32)
    (local.set $pos (call $rt.put2 (local.get $o) (local.get $pos) (local.get $letter)))
    (block $done
      (loop $l
        (br_if $done (i32.eqz (local.get $digits)))
        (local.set $digits (i32.sub (local.get $digits) (i32.const 1)))
        (local.set $pos (call $rt.put (local.get $o) (local.get $pos)
          (call $rt.hexdigit (i32.and (i32.shr_u (local.get $v) (i32.shl (local.get $digits) (i32.const 2)))
                                      (i32.const 15)))))
        (br $l)))
    (local.get $pos))

  ;; backslash followed by one letter
  (func $rt.put2 (param $o (ref $u8s)) (param $pos i32) (param $b i32) (result i32)
    (call $rt.put (local.get $o) (call $rt.put (local.get $o) (local.get $pos) (i32.const 92)) (local.get $b)))

  (func $rt.str_repr (param $s (ref $str)) (result (ref $str))
    (local $a (ref $u8s)) (local $n i32) (local $i i32) (local $c i32) (local $q i32) (local $k i32)
    (local $sq i32) (local $dq i32) (local $o (ref $u8s)) (local $pos i32) (local $r (ref $u8s)) (local $cp i32)
    (local.set $a (struct.get $str $bytes (local.get $s)))
    (local.set $n (array.len (local.get $a)))
    (block $d1
      (loop $l1
        (br_if $d1 (i32.ge_u (local.get $i) (local.get $n)))
        (local.set $c (array.get_u $u8s (local.get $a) (local.get $i)))
        (if (i32.eq (local.get $c) (i32.const 39)) (then (local.set $sq (i32.const 1))))
        (if (i32.eq (local.get $c) (i32.const 34)) (then (local.set $dq (i32.const 1))))
        (local.set $i (i32.add (local.get $i) (i32.const 1)))
        (br $l1)))
    (local.set $q (select (i32.const 34) (i32.const 39) (i32.and (local.get $sq) (i32.eqz (local.get $dq)))))
    ;; worst case: an ASCII control byte becomes \xNN (4 bytes), plus the quotes
    (local.set $o (array.new_default $u8s (i32.add (i32.mul (local.get $n) (i32.const 4)) (i32.const 2))))
    (local.set $pos (call $rt.put (local.get $o) (i32.const 0) (local.get $q)))
    (local.set $i (i32.const 0))
    (block $d2
      (loop $l2
        (br_if $d2 (i32.ge_u (local.get $i) (local.get $n)))
        (local.set $c (array.get_u $u8s (local.get $a) (local.get $i)))
        (local.set $k (i32.const 1))
        (block $next
          (if (i32.ge_u (local.get $c) (i32.const 0x80))
            (then
              (local.set $cp (call $rt.dec (local.get $s) (local.get $i)))
              (local.set $k (call $rt.u8w (local.get $c)))
              (if (i32.and (call $rt.flags_hi (local.get $cp)) (i32.const 1))  ;; NONPRINT
                (then
                  (local.set $pos
                    (if (result i32) (i32.le_u (local.get $cp) (i32.const 0xFF))
                      (then (call $rt.put_escape (local.get $o) (local.get $pos) (i32.const 120) (local.get $cp) (i32.const 2)))
                      (else (if (result i32) (i32.le_u (local.get $cp) (i32.const 0xFFFF))
                        (then (call $rt.put_escape (local.get $o) (local.get $pos) (i32.const 117) (local.get $cp) (i32.const 4)))
                        (else (call $rt.put_escape (local.get $o) (local.get $pos) (i32.const 85) (local.get $cp) (i32.const 8))))))))
                (else
                  (array.copy $u8s $u8s (local.get $o) (local.get $pos) (local.get $a) (local.get $i) (local.get $k))
                  (local.set $pos (i32.add (local.get $pos) (local.get $k)))))
              (br $next)))
          (if (i32.or (i32.eq (local.get $c) (local.get $q)) (i32.eq (local.get $c) (i32.const 92)))
            (then (local.set $pos (call $rt.put2 (local.get $o) (local.get $pos) (local.get $c))) (br $next)))
          (if (i32.eq (local.get $c) (i32.const 10))
            (then (local.set $pos (call $rt.put2 (local.get $o) (local.get $pos) (i32.const 110))) (br $next)))
          (if (i32.eq (local.get $c) (i32.const 13))
            (then (local.set $pos (call $rt.put2 (local.get $o) (local.get $pos) (i32.const 114))) (br $next)))
          (if (i32.eq (local.get $c) (i32.const 9))
            (then (local.set $pos (call $rt.put2 (local.get $o) (local.get $pos) (i32.const 116))) (br $next)))
          (if (i32.or (i32.lt_u (local.get $c) (i32.const 32)) (i32.eq (local.get $c) (i32.const 127)))
            (then
              (local.set $pos (call $rt.put_escape (local.get $o) (local.get $pos) (i32.const 120) (local.get $c) (i32.const 2)))
              (br $next)))
          (local.set $pos (call $rt.put (local.get $o) (local.get $pos) (local.get $c))))
        (local.set $i (i32.add (local.get $i) (local.get $k)))
        (br $l2)))
    (local.set $pos (call $rt.put (local.get $o) (local.get $pos) (local.get $q)))
    (local.set $r (array.new_default $u8s (local.get $pos)))
    (array.copy $u8s $u8s (local.get $r) (i32.const 0) (local.get $o) (i32.const 0) (local.get $pos))
    ;; escapes are ASCII, so an ASCII string has an ASCII repr
    (call $rt.mkstr (local.get $r)
      (select (local.get $pos) (i32.const -1) (i32.eq (struct.get $str $cp (local.get $s)) (local.get $n)))))

  (func $rt.opt_str_repr (param $s (ref null $str)) (result (ref $str))
    (if (result (ref $str)) (ref.is_null (local.get $s))
      (then (str.lit "None"))
      (else (call $rt.str_repr (ref.as_non_null (local.get $s))))))

  ;; ---- exceptions ---------------------------------------------------------
  ;; Exception objects are instances of the classes in snekc/builtins.py; the
  ;; message is the hidden field $__message (null when the exception was created
  ;; without an argument) and the class name is the first field of every vtable.

  ;; str(e): the message; '' without one; KeyError shows repr(key) like CPython
  (func $rt.exc_str (param $e (ref $BaseException)) (result (ref $str))
    (local $m (ref null $str))
    (local.set $m (struct.get $BaseException $__message (local.get $e)))
    (if (ref.is_null (local.get $m)) (then (return (str.lit ""))))
    (if (ref.test (ref $KeyError) (local.get $e))
      (then (return (call $rt.str_repr (ref.as_non_null (local.get $m))))))
    (ref.as_non_null (local.get $m)))

  ;; repr(e): Name('message'), or Name() without one
  (func $rt.exc_repr (param $e (ref $BaseException)) (result (ref $str))
    (local $m (ref null $str))
    (local.set $m (struct.get $BaseException $__message (local.get $e)))
    (call $rt.str_concat_n (array.new_fixed $strs 4
      (struct.get $BaseException.vt $__name__ (struct.get $BaseException $__vt__ (local.get $e)))
      (str.lit "(")
      (if (result (ref $str)) (ref.is_null (local.get $m))
        (then (str.lit ""))
        (else (call $rt.str_repr (ref.as_non_null (local.get $m)))))
      (str.lit ")"))))

  ;; An exception escaped the program: print `Name: message` (CPython's last
  ;; traceback line, just `Name` when str(e) is empty) to stderr and exit 1.
  (func $rt.uncaught (param $e (ref $BaseException))
    (local $name (ref $str)) (local $msg (ref $str))
    (local.set $name (struct.get $BaseException.vt $__name__ (struct.get $BaseException $__vt__ (local.get $e))))
    (local.set $msg (call $rt.exc_str (local.get $e)))
    (call $rt.panic
      (if (result (ref $str)) (call $rt.str_len (local.get $msg))
        (then (call $rt.str_concat (call $rt.str_concat (local.get $name) (str.lit ": ")) (local.get $msg)))
        (else (local.get $name)))))
