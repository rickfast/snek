;; Temporary inbound canonical data lives above the borrowed CLI scratch page.
;; Reset only after every returned value has been copied to the GC heap.
(global $abi.bump (mut i32) (i32.const 65536))

(func $abi.reset (global.set $abi.bump (i32.const 65536)))

(func $abi.alloc (param $n i32) (param $align i32) (result i32)
  (local $ptr i64) (local $end i64) (local $pages i32)
  (if (i32.eqz (local.get $n)) (then (return (i32.const 0))))
  (if (i32.or (i32.eqz (local.get $align))
              (i32.and (local.get $align) (i32.sub (local.get $align) (i32.const 1)))) (then (unreachable)))
  (local.set $ptr (i64.and
    (i64.add (i64.extend_i32_u (global.get $abi.bump)) (i64.extend_i32_u (i32.sub (local.get $align) (i32.const 1))))
    (i64.xor (i64.extend_i32_u (i32.sub (local.get $align) (i32.const 1))) (i64.const -1))))
  (local.set $end (i64.add (local.get $ptr) (i64.extend_i32_u (local.get $n))))
  (if (i64.gt_u (local.get $end) (i64.const 4294967295)) (then (unreachable)))
  (local.set $pages (i32.wrap_i64 (i64.shr_u (i64.add (local.get $end) (i64.const 65535)) (i64.const 16))))
  (if (i32.gt_u (local.get $pages) (memory.size))
    (then (if (i32.eq (memory.grow (i32.sub (local.get $pages) (memory.size))) (i32.const -1)) (then (unreachable)))))
  (global.set $abi.bump (i32.wrap_i64 (local.get $end)))
  (i32.wrap_i64 (local.get $ptr)))

(func $abi.realloc (param $old i32) (param $old-size i32) (param $align i32) (param $size i32) (result i32)
  (local $new i32) (local $n i32) (local $i i32)
  (if (i32.eqz (local.get $size)) (then (return (i32.const 0))))
  (local.set $new (call $abi.alloc (local.get $size) (local.get $align)))
  (if (local.get $old)
    (then
      (local.set $n (if (result i32) (i32.lt_u (local.get $size) (local.get $old-size))
        (then (local.get $size)) (else (local.get $old-size))))
      (block $done (loop $copy
        (br_if $done (i32.ge_u (local.get $i) (local.get $n)))
        (i32.store8 (i32.add (local.get $new) (local.get $i)) (i32.load8_u (i32.add (local.get $old) (local.get $i))))
        (local.set $i (i32.add (local.get $i) (i32.const 1))) (br $copy)))))
  (local.get $new))
