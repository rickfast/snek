# wpy design notes

The goal is Python syntax with static semantics. A wpy program should read as ordinary typed Python, run unchanged under CPython (with the `wpy` shim), and compile to small, fast WasmGC modules without a Python runtime. Where Python's dynamic semantics and a static compiler disagree, wpy rejects the program at compile time rather than silently behaving differently. The divergences that remain are listed at the end.

## Types and inference

- **Annotations.** Function signatures and class fields must be annotated. Locals are inferred from their first assignment and keep that type.
- **Inference is local and bidirectional.** An annotation, parameter type or field type flows down into the expression being checked, so `xs: list[i64] = [1, 2]` makes the literals i64. No Hindley–Milner is needed, and type errors stay local to the line that caused them.
- **Numeric literals take their type from context**, in this order:
  - an annotation or parameter type;
  - otherwise the other operand, so `x + 1` with `x: i64` is an i64 add;
  - otherwise the default, i32 for integers and f64 for floats.
- **Gotcha: arithmetic is computed in the operands' type, then widened.** With `small: i32`, `big: i64 = small * 50000` multiplies in i32 and widens the result, as in C. Write `i64(small) * 50000` to multiply in i64. A future lint could flag widening the result of an arithmetic expression.
- **Definite assignment is checked conservatively.**
  - A variable assigned on only one branch can't be read after the `if`.
  - A variable first assigned inside a loop body can't be read after the loop.

## Numbers

- **Types.** `i32`, `i64`, `f32` and `f64` are imported from `wpy`; `bool` is built in. `int` and `float` are rejected with a hint. Numbers are never boxed.
- **Implicit conversions** are limited to the lossless widenings i32→i64, i32→f64 and f32→f64. Everything else is explicit: `i32(x)`, `f64(n)` and so on.
  - Float-to-int conversion uses the saturating truncation instructions, so it never traps.
- **Integer overflow wraps**, matching the native Wasm instructions. Checked arithmetic is a candidate opt-in.
- **Division follows Python.**
  - `//` floors and `%` takes the sign of the divisor, implemented in `prelude.wat`; Wasm's `div_s` truncates.
  - `/` always produces a float. i32 operands widen to f64. i64 operands are rejected because the conversion could lose precision.
  - Division by zero is checked explicitly and raises `ZeroDivisionError`, because a Wasm divide-by-zero trap can't be caught.
  - `INT_MIN // -1` wraps instead of trapping.
- **No implicit truthiness.** Conditions must be `bool`. The error message suggests `x != 0`, `len(xs) > 0` or `x is not None`.

## Reference types and None

- `str`, `list[T]` and classes are references to GC structs and are non-null by default.
- `T | None` (or `Optional[T]`) is allowed only for reference types and maps to `(ref null $T)`. `i32 | None` is rejected because it would need boxing.
- **Narrowing is flow-sensitive and applies to locals only.** It is triggered by:
  - `is None` / `is not None` checks;
  - `isinstance`;
  - `assert`;
  - early `return`/`break`/`continue`;
  - the operands of `and`/`or`;
  - conditional expressions.
- **Fields are never narrowed.** Another alias could change a field between the check and the use, so you copy the field into a local first. This is Kotlin's rule.
- A narrowed read compiles to `ref.as_non_null`, or to `ref.cast` when narrowed to a subclass. `wasm-opt` removes most of these.

## Classes

- **Classes are reference types only**, with no value structs. WasmGC has no inline struct fields, so value types would mean flattening into locals; that's a later optimization, not part of the language.
- **Single inheritance** maps to WasmGC struct subtyping. A subclass struct repeats its parent's fields as a prefix and refines the vtable field's type.
  ```
  (type $Shape  (sub (struct (field $__vt__ (ref $Shape.vt)) (field $name (ref $str)) ...)))
  (type $Circle (sub final $Shape (struct (field $__vt__ (ref $Circle.vt)) ... (field $r (mut f64)))))
  ```
- **Vtables.** Each class has a vtable struct of typed funcrefs and one immutable global instance.
  - A slot's function type takes the class that introduced the method as `self`.
  - An override downcasts `self` once on entry.
  - Overrides must match the overridden signature exactly.
- **Closed world.** The whole program is compiled together. Classes without subclasses are emitted `final`, and calls are devirtualized when every class reachable from the receiver's static type shares one implementation. `wasm-opt --closed-world` then refines and merges types further.
- **`Final[T]` fields** become immutable struct fields and can only be assigned in `__init__`'s field prefix.
- **`__init__` uses definite initialization.** `struct.new` needs every field value at once, and non-null reference fields have no default. So:
  - leading statements of the form `self.f = <expr that doesn't use self>` are evaluated into temporaries;
  - other statements that don't touch `self` may be interleaved with them;
  - the first statement that uses `self` allocates the object, and every field must have a value by then, either assigned or from a literal default in the class body;
  - the rest of `__init__` runs on the real object.

  This is Swift's two-phase initialization, minus `super.init`.
- **Inherited `__init__`.** A subclass without its own `__init__` inherits its parent's, which is re-checked with `self` typed as the subclass.
- **Not yet supported:** `super()` calls, special methods (`__eq__`, `__str__`, …), properties, and static or class methods.

## Strings

- **Layout.** `(struct (field $bytes (ref $u8s)) (field $hash (mut i32)))` holds UTF-8 bytes plus an FNV-1a hash that is computed lazily and cached. The hash is unused until dicts arrive.
- **Literals** are bytes in one passive data segment. Each literal compiles to a function that builds the string with `array.new_data` on first use and caches it in a global. Strings are immutable, so sharing literal instances is safe.
- **One mutable byte-array type** backs `str` (and will back `bytes` and string builders); immutability is enforced by the language. A mutable array type can't be a subtype of an immutable one, so separate types would force copies.
- **`len(s)` currently counts bytes.**
- **Open decision: indexing.** Either Python's code-point semantics with an ASCII-only fast-path flag and a lazily built index (PyPy's approach), or Rust-style byte offsets. Indexing and iteration over `str` are rejected until this is settled.

## Lists

- `list[T]` is monomorphized: `(struct (field $len (mut i32)) (field $data (mut (ref $arr.T))))`. `list[f64]` stores raw f64s and `list[bool]` uses a packed `(array (mut i8))`.
- **Reference elements** are stored nullable so capacity can be pre-allocated with `array.new_default`. Non-optional element types are read back with `ref.as_non_null`, and `pop` clears the slot so the GC can reclaim it.
- **Growth** doubles the backing array with `array.copy`. Indexing is bounds-checked and supports negative indices.

## The WASI boundary

- **Linear memory is used only for I/O.** WASI preview1's `fd_write` takes a pointer and length, so the module keeps one page of linear memory.
- **The copy is a byte loop.** There is no bulk GC-array↔memory instruction, so stdout is buffered there and flushed once every 63 KiB and at exit.
- **The plan is a component with WASI 0.3.** Its async ABI fits `async def` lowered to state machines.
  - Strings will still be copied at the boundary until the canonical ABI's GC option is usable in Wasmtime.

## Runtime errors

- Runtime errors panic: flush stdout, write `IndexError: …` to stderr, and exit with status 1. That's observably the same as an uncaught CPython exception, minus the traceback.
- Panics become `throw` once exceptions land. Wasm exception handling is on by default in Wasmtime 47+.

## Known divergences from CPython

These are accepted on purpose or still pending:

- **Integer width.** Arithmetic wraps at 32 or 64 bits; CPython integers are unbounded.
- **`len(str)` counts UTF-8 bytes**, pending the indexing decision above.
- **Float repr.** `str(float)` uses the shortest of 15, 16 or 17 significant digits that round-trips. That matches CPython for ordinary values; a full Ryu or Grisu port would make it exact.
- **Float `%`** uses `a - b*trunc(a/b)`, because Wasm has no fmod. It can differ for very large magnitudes.
- **`f32` values** print as their f64 expansion.
