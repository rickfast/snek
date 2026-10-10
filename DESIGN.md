# snek design notes

The goal is Python syntax with static semantics. A snek program should read as ordinary typed Python, run unchanged under CPython (with the `snek` shim), and compile to small, fast WasmGC modules without a Python runtime. Where Python's dynamic semantics and a static compiler disagree, snek rejects the program at compile time rather than silently behaving differently. The divergences that remain are listed at the end.

## Types and inference

- **Annotations.** Function signatures and class fields must be annotated. Locals are inferred from their first assignment and keep that type.
- **Inference is local and bidirectional.** An annotation, parameter type or field type flows down into the expression being checked, so `xs: list[i64] = [1, 2]` makes the literals i64. No Hindley–Milner is needed, and type errors stay local to the line that caused them.
- **Numeric literals take their type from context**, in this order:
  - an annotation or parameter type;
  - otherwise the other operand, so `x + 1` with `x: i64` is an i64 add and `y * 2` with `y: f64` is an f64 multiply;
  - otherwise the default, i32 for integers and f64 for floats.

  An integer literal never becomes a float just because the context is a float: `x: f64 = 2` and `x: f64 = 2 + 3` are rejected, because CPython keeps those values as ints and would print `2`, not `2.0`. Write `2.0`.
- **Gotcha: arithmetic is computed in the operands' type, then widened.** With `small: i32`, `big: i64 = small * 50000` multiplies in i32 and widens the result, as in C. Write `i64(small) * 50000` to multiply in i64. A future lint could flag widening the result of an arithmetic expression.
- **Definite assignment is checked conservatively.** A read is rejected unless the variable is assigned on every path to it:
  - a variable assigned on only one branch can't be read after the `if`;
  - a variable first assigned inside a loop body can't be read after the loop;
  - CPython deletes the name in `except E as e` when the handler ends, so `e` is unassigned afterwards, even if it held something before the `try`. That includes outer handlers, `finally` blocks and the next loop iteration;
  - a handler can start after any prefix of its `try` body, so it only sees what was assigned before the `try`.

  Where CPython would raise `UnboundLocalError` at run time, snek rejects the program at compile time.

## Numbers

- **Types.** `i32`, `i64`, `f32` and `f64` are imported from `snek`; `bool` is built in. `int` and `float` are rejected with a hint. Numbers are never boxed.
- **Implicit conversions** are limited to these cases:
  - lossless int widening (i32→i64) and float widening (f32→f64), anywhere;
  - int→float, but only inside arithmetic and comparisons, where CPython's result is a float too.

  Storing, passing or returning an int where a float is expected needs `f64(n)`. So does `min`/`max` over mixed operands, since CPython's `max(2, 1.5)` returns the int `2`. Everything else is explicit: `i32(x)`, `f64(n)` and so on.
- **Mixed conditional expressions.** In `1 if c else 2.5`, CPython's value is an int on one path. snek types it as f64 and allows it as an arithmetic or comparison operand. It is rejected wherever the value itself would be kept or shown: assigned, passed, returned, printed, converted with `str()`, or inspected with `type()`.
- **Float→int conversions are checked.** `i32(x)` and `i64(x)` truncate toward zero like `int(x)`.
  - NaN raises `ValueError` and infinity raises `OverflowError`, with CPython's messages.
  - A value that doesn't fit the target width raises `OverflowError`, where CPython would return a big int.
- **Integer overflow wraps**, matching the native Wasm instructions; so does narrowing `i32(n)` of an i64. Checked arithmetic is a candidate opt-in.
- **Division follows Python.**
  - `//` floors and `%` takes the sign of the divisor, implemented in `prelude.wat`; Wasm's `div_s` truncates.
  - `/` always produces a float. i32 operands widen to f64. i64 operands are rejected because the conversion could lose precision.
  - Division by zero is checked explicitly and raises `ZeroDivisionError` with CPython's message for each case, because a Wasm divide-by-zero trap can't be caught.
  - `INT_MIN // -1` wraps instead of trapping.
- **No implicit truthiness.** Conditions must be `bool`. The error message suggests `x != 0`, `len(xs) > 0` or `x is not None`.

## Functions and calls

- **Default values** must be literals (numbers, strings, bools, `None`). CPython evaluates defaults once, so anything mutable would be shared across calls. Defaults are filled in at the call site from the signature of the statically known callee.
- **Keyword arguments** are matched by name, with CPython's errors for unknown, duplicate and missing arguments. `*args`, `**kwargs` and keyword-only parameters aren't supported yet.
- **Evaluation order matches CPython**, which takes care in a few places:
  - Keyword arguments written out of parameter order are evaluated left to right into temporaries.
  - In `xs[i] = f()` and `obj.f = g()`, the right-hand side is evaluated before the target and index.
  - `print(a, b)` evaluates every argument before writing anything, and runs `str()` at write time. A list printed next to a call that mutates it shows the mutation, and an argument that raises leaves no partial output.

## Reference types and None

- `str`, `list[T]` and classes are references to GC structs and are non-null by default.
- `T | None` (or `Optional[T]`) is allowed only for reference types and maps to `(ref null $T)`. `i32 | None` is rejected because it would need boxing.
- **Narrowing is flow-sensitive and applies to locals only.** It is triggered by:
  - `is None` / `is not None` checks;
  - `isinstance`;
  - `assert`;
  - early `return`/`break`/`continue`/`raise`;
  - the operands of `and`/`or`;
  - conditional expressions;
  - `except E as e`, which narrows `e` to `E`;
  - assigning a non-null value to an optional local.
- **Fields are never narrowed.** Another alias could change a field between the check and the use, so you copy the field into a local first. This is Kotlin's rule.
- A narrowed read compiles to `ref.as_non_null`, or to `ref.cast` when narrowed to a subclass. `wasm-opt` removes most of these.

## Classes

- **Classes are reference types only**, with no value structs. WasmGC has no inline struct fields, so value types would mean flattening into locals; that's a later optimization, not part of the language.
- **Single inheritance** maps to WasmGC struct subtyping. A subclass struct repeats its parent's fields as a prefix and refines the vtable field's type.
  ```
  (type $Shape  (sub (struct (field $__vt__ (ref $Shape.vt)) (field $name (ref $str)) ...)))
  (type $Circle (sub final $Shape (struct (field $__vt__ (ref $Circle.vt)) ... (field $r (mut f64)))))
  ```
- **Vtables.** Each class has a vtable struct holding the class name (for `type(x).__name__` and error messages) and typed funcrefs, with one immutable global instance per class.
  - A slot's function type takes the class that introduced the method as `self`.
  - An override downcasts `self` once on entry.
  - Overrides must keep the same parameter names, types, defaults and return type. Callers bind keyword arguments and fill defaults from the static receiver type, so all of them have to agree.
- **Closed world.** The whole program is compiled together. Classes without subclasses are emitted `final`, and calls are devirtualized when every class reachable from the receiver's static type shares one implementation. `wasm-opt --closed-world` then refines and merges types further.
- **`Final[T]` fields** become immutable struct fields and can only be assigned before the object exists (see below).
- **`__init__` uses two-phase initialization, like Swift.** `struct.new` needs every field value at once, and non-null reference fields have no default.
  - **Phase 1 runs before the object exists:**
    - `self.f = <expr that doesn't use self>` statements are evaluated into temporaries;
    - other statements that don't touch `self` may be interleaved with them;
    - `super().__init__(...)` binds its arguments and checks the base class's `__init__` body in place, with its locals renamed apart, as part of the same phase.
  - **The first statement that uses `self` allocates the object.** By then every field, inherited ones included, must have a value, either assigned or from a literal default in the class body.
  - **Phase 2 is the rest of `__init__`**, which runs on the real object. A `return` inside an inlined base `__init__` leaves that block and continues in the subclass.
  - **A `return` before `self` is used** allocates the object on that path, provided every field already has a value. Inside an inlined base `__init__` it is allowed only when nothing after it in that `__init__` uses `self` or assigns a field. That way the statements it skips can't be the ones that build the object.
  - **Consequence:** `super().__init__()` must come before the subclass first uses `self`. When it doesn't, the error names that line. Calling `super().__init__()` with no arguments on a class without a base is allowed and does nothing.
- **`super().method(...)`** is a direct call to the base implementation, with no vtable lookup.
- **`isinstance(x, C)`** is a `ref.test`. When the static type already guarantees the answer it becomes a constant (plus a null check for optionals). When `C` is unrelated to `x`'s declared type, the call is rejected as always False.
- **Inherited `__init__`.** A subclass without its own `__init__` inherits its parent's, which is re-checked with `self` typed as the subclass.
- **Not yet supported:** special methods (`__eq__`, `__str__`, …), properties, and static or class methods.

## Exceptions

- **The hierarchy is written in snek** (`snekc/builtins.py`) and declared ahead of every program:
  - `BaseException` → `Exception`, which has these subclasses:
    - `ArithmeticError` (`ZeroDivisionError`, `OverflowError`)
    - `AssertionError`
    - `LookupError` (`IndexError`, `KeyError`)
    - `RuntimeError` (`NotImplementedError`)
    - `ValueError`
    - `TypeError`
  - User exceptions are ordinary subclasses.
  - The message is a hidden field, so it is read the CPython way, through `str(e)`. It is null when the exception was created without an argument, which is how `str(KeyError())` (`''`) differs from `str(KeyError(""))` (`"''"`).
  - `KeyError` shows `repr(key)`, with CPython's quoting and escapes.
- **One tag** carries every exception: `(tag $snek.exc (param (ref $BaseException)))`. `raise` is `throw`.
- **`try`/`except` lowers to a `try_table` that catches the tag into a block**, followed by one `ref.test` per handler on the caught object. That's the same test as `isinstance`, so handlers follow the class hierarchy, and a tuple of classes is an `or` of tests. When no handler matches, the exception is rethrown. `else` runs after the `try_table`, so its exceptions aren't caught by these handlers.
- **`finally` uses a pending-action lowering.** Every way out of the protected region branches to one place:
  - fallthrough, `return` (the value is saved first), `break` and `continue` record a small code in a local;
  - an escaping exception is caught and saved;
  - the finally body runs once, outside the `try_table`, so an exception raised inside it propagates normally;
  - finally, the pending action is re-issued in the enclosing context, possibly through further `finally` regions. Exceptions are rethrown, returns return and loops break or continue.

  The body is emitted once, not duplicated at every exit.
- **`return`, `break` and `continue` out of a `finally` are rejected.** They would swallow any in-flight exception. CPython 3.14 warns about this (PEP 765).
- **Bare `raise`** re-raises the exception of the innermost enclosing `except` block. It is rejected outside handlers and inside `finally` blocks, where the exception it would re-raise depends on the path taken.
- **Runtime errors are real exceptions with CPython's messages**, and they are catchable:
  - list index out of range and pop from empty list;
  - integer and float division or modulo by zero;
  - failed `assert`, which evaluates its message lazily;
  - float→int conversion of NaN, infinity or out-of-range values.
- **Uncaught exceptions** flush stdout and write `Name: message` (just `Name` when `str(e)` is empty) to stderr. That matches the last line of a CPython traceback. Exit status is 1.
- **Not yet supported:**
  - `raise ... from ...` and exception chaining;
  - `except*`;
  - `with`;
  - the `args`, `with_traceback` and `add_note` attributes (these names are reserved on exception classes so they can't silently mean something else);
  - exceptions created with more than one argument or with a non-`str` argument.
- **Exception `__init__` must call `super().__init__(message)`.** CPython derives `str(e)` from the constructor arguments when it is skipped, which snek doesn't emulate.

## Strings

- **Layout.** `(struct (field $bytes (ref $u8s)) (field $hash (mut i32)))` holds UTF-8 bytes plus an FNV-1a hash that is computed lazily and cached. The hash is unused until dicts arrive.
- **Literals** are bytes in one passive data segment. Each literal compiles to a function that builds the string with `array.new_data` on first use and caches it in a global. Strings are immutable, so sharing literal instances is safe.
- **One mutable byte-array type** backs `str` (and will back `bytes` and string builders); immutability is enforced by the language. A mutable array type can't be a subtype of an immutable one, so separate types would force copies.
- **`repr`** follows CPython's quote choice and escapes: `\\`, `\n`, `\r`, `\t`, and every other non-printable code point as `\xNN`, `\uNNNN` or `\UNNNNNNNN`. It's used for list elements and `KeyError`.
  - Printability normally needs Unicode tables. A snek program reads no input, though, so every string it can build comes from its own literals, formatted numbers and class names. The compiler therefore embeds only the non-printable code points that occur in the program's literals, which is usually none.
  - When input APIs arrive, the full table (about 700 ranges) will come with them.
  - String literals containing lone surrogates are rejected, because they are not valid UTF-8.
- **`len(s)` currently counts bytes.**
- **Open decision: indexing.** Either Python's code-point semantics with an ASCII-only fast-path flag and a lazily built index (PyPy's approach), or Rust-style byte offsets. Indexing and iteration over `str` are rejected until this is settled.

## Lists

- `list[T]` is monomorphized: `(struct (field $len (mut i32)) (field $data (mut (ref $arr.T))))`. `list[f64]` stores raw f64s and `list[bool]` uses a packed `(array (mut i8))`.
- **Generated names** use `<`, `>` and `?` (for example `$list.list<Node?>`). Those characters are legal in WAT but not in Python identifiers, so they can't collide with user names.
- **Reference elements** are stored nullable so capacity can be pre-allocated with `array.new_default`. Non-optional element types are read back with `ref.as_non_null`, and `pop` clears the slot so the GC can reclaim it.
- **Growth** doubles the backing array with `array.copy`. Indexing is bounds-checked and supports negative indices.
- **`str()` of a list** prints its elements with `repr`, like CPython. Element types can be numbers, bools, strings, optional strings, or lists of those.

## The WASI boundary

- **Linear memory is used only for I/O.** WASI preview1's `fd_write` takes a pointer and length, so the module keeps one page of linear memory.
- **The copy is a byte loop.** There is no bulk GC-array↔memory instruction, so stdout is buffered there and flushed once every 63 KiB, at exit, and before an uncaught exception is reported.
- **The plan is a component with WASI 0.3.** Its async ABI fits `async def` lowered to state machines.
  - Strings will still be copied at the boundary until the canonical ABI's GC option is usable in Wasmtime.

## Engines

- **Wasmtime 47+** runs everything with its defaults; GC and exception handling are both on.
- **Node 24+** (V8 13.x) also works. Node 22's V8 12.4 mis-executes a `try_table` that produces a value. `wasm-opt -O2` and above generate exactly that when they turn a caught `throw` into a branch, so the runner refuses Node versions before 24.

## Known divergences from CPython

These are accepted on purpose or still pending:

- **Integer width.** Arithmetic wraps at 32 or 64 bits, while CPython integers are unbounded. Float→int conversions that don't fit raise `OverflowError` instead of producing a big int.
- **`len(str)` counts UTF-8 bytes**, pending the indexing decision above.
- **Float repr.** `str(float)` uses the shortest of 15, 16 or 17 significant digits that round-trips. That matches CPython for ordinary values; a full Ryu or Grisu port would make it exact.
- **Float `%`** uses `a - b*trunc(a/b)`, because Wasm has no fmod. It can differ for very large magnitudes.
- **`f32` values** print as their f64 expansion.
- **Traps.** Stack overflow is a Wasm trap rather than a catchable `RecursionError`. A trap also loses whatever stdout is still buffered.
