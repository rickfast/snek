  ;; ======================================================================
  ;; snek runtime prelude
  ;;
  ;; Spliced into every compiled module by snekc/emit_wat.py. The types it
  ;; uses ($u8s, $str) are declared by the emitter inside the module's rec
  ;; group. Forms written as (str.lit "...") are rewritten by the emitter into
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

  ;; ---- strings: UTF-8 bytes in a GC array + cached hash ------------------

  (func $rt.str_len (param $s (ref $str)) (result i32)
    (array.len (struct.get $str $bytes (local.get $s))))

  (func $rt.str_concat (param $x (ref $str)) (param $y (ref $str)) (result (ref $str))
    (local $a (ref $u8s)) (local $b (ref $u8s)) (local $r (ref $u8s)) (local $n i32)
    (local.set $a (struct.get $str $bytes (local.get $x)))
    (local.set $b (struct.get $str $bytes (local.get $y)))
    (local.set $n (array.len (local.get $a)))
    (local.set $r (array.new_default $u8s (i32.add (local.get $n) (array.len (local.get $b)))))
    (array.copy $u8s $u8s (local.get $r) (i32.const 0) (local.get $a) (i32.const 0) (local.get $n))
    (array.copy $u8s $u8s (local.get $r) (local.get $n) (local.get $b) (i32.const 0) (array.len (local.get $b)))
    (struct.new $str (local.get $r) (i32.const 0)))

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

  (func $rt.i64_to_str (param $v i64) (result (ref $str))
    (local $buf (ref $u8s)) (local $out (ref $u8s)) (local $pos i32) (local $u i64) (local $neg i32)
    (local.set $buf (array.new_default $u8s (i32.const 20)))
    (local.set $pos (i32.const 20))
    (local.set $neg (i64.lt_s (local.get $v) (i64.const 0)))
    ;; 0 - INT64_MIN wraps to itself, which is 2^63 read as unsigned: correct.
    (local.set $u (select (i64.sub (i64.const 0) (local.get $v)) (local.get $v) (local.get $neg)))
    (loop $l
      (local.set $pos (i32.sub (local.get $pos) (i32.const 1)))
      (array.set $u8s (local.get $buf) (local.get $pos)
        (i32.add (i32.const 48) (i32.wrap_i64 (i64.rem_u (local.get $u) (i64.const 10)))))
      (local.set $u (i64.div_u (local.get $u) (i64.const 10)))
      (br_if $l (i64.ne (local.get $u) (i64.const 0))))
    (if (local.get $neg)
      (then
        (local.set $pos (i32.sub (local.get $pos) (i32.const 1)))
        (array.set $u8s (local.get $buf) (local.get $pos) (i32.const 45))))
    (local.set $out (array.new_default $u8s (i32.sub (i32.const 20) (local.get $pos))))
    (array.copy $u8s $u8s (local.get $out) (i32.const 0) (local.get $buf) (local.get $pos)
                (i32.sub (i32.const 20) (local.get $pos)))
    (struct.new $str (local.get $out) (i32.const 0)))

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
    (struct.new $str (local.get $r) (i32.const 0)))

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
  ;;
  ;; Printability needs Unicode tables, but a snek program has no input: every
  ;; string it can build comes from its own literals, numbers and class names.
  ;; So the compiler emits only the non-printable code points that occur in
  ;; the program's literals, as a sorted table ($nonprint, $rt.np.n entries),
  ;; which is usually empty.

  (global $rt.np.cache (mut (ref null $u32s)) (ref.null $u32s))

  (func $rt.nonprint (param $cp i32) (result i32)
    (local $t (ref $u32s)) (local $lo i32) (local $hi i32) (local $mid i32) (local $v i32)
    (if (i32.eqz (global.get $rt.np.n)) (then (return (i32.const 0))))
    (if (ref.is_null (global.get $rt.np.cache))
      (then (global.set $rt.np.cache (array.new_data $u32s $nonprint (i32.const 0) (global.get $rt.np.n)))))
    (local.set $t (ref.as_non_null (global.get $rt.np.cache)))
    (local.set $hi (array.len (local.get $t)))
    (block $done
      (loop $search
        (br_if $done (i32.ge_u (local.get $lo) (local.get $hi)))
        (local.set $mid (i32.shr_u (i32.add (local.get $lo) (local.get $hi)) (i32.const 1)))
        (local.set $v (array.get $u32s (local.get $t) (local.get $mid)))
        (if (i32.eq (local.get $v) (local.get $cp)) (then (return (i32.const 1))))
        (if (i32.lt_u (local.get $v) (local.get $cp))
          (then (local.set $lo (i32.add (local.get $mid) (i32.const 1))))
          (else (local.set $hi (local.get $mid))))
        (br $search)))
    (i32.const 0))

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
        (block $next
          (if (i32.ge_u (local.get $c) (i32.const 0x80))
            (then
              ;; decode one UTF-8 sequence (literals are valid UTF-8; the checker rejects surrogates)
              (local.set $k (select (i32.const 4) (select (i32.const 3) (i32.const 2)
                                                          (i32.ge_u (local.get $c) (i32.const 0xE0)))
                                    (i32.ge_u (local.get $c) (i32.const 0xF0))))
              (local.set $cp (i32.and (local.get $c) (select (i32.const 0x07) (select (i32.const 0x0F) (i32.const 0x1F)
                                                          (i32.eq (local.get $k) (i32.const 3)))
                                                          (i32.eq (local.get $k) (i32.const 4)))))
              (local.set $c (i32.const 1))
              (block $dd
                (loop $dl
                  (br_if $dd (i32.ge_u (local.get $c) (local.get $k)))
                  (local.set $cp (i32.or (i32.shl (local.get $cp) (i32.const 6))
                    (i32.and (array.get_u $u8s (local.get $a) (i32.add (local.get $i) (local.get $c))) (i32.const 0x3F))))
                  (local.set $c (i32.add (local.get $c) (i32.const 1)))
                  (br $dl)))
              (if (call $rt.nonprint (local.get $cp))
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
              (local.set $i (i32.add (local.get $i) (i32.sub (local.get $k) (i32.const 1))))
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
        (local.set $i (i32.add (local.get $i) (i32.const 1)))
        (br $l2)))
    (local.set $pos (call $rt.put (local.get $o) (local.get $pos) (local.get $q)))
    (local.set $r (array.new_default $u8s (local.get $pos)))
    (array.copy $u8s $u8s (local.get $r) (i32.const 0) (local.get $o) (i32.const 0) (local.get $pos))
    (struct.new $str (local.get $r) (i32.const 0)))

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
