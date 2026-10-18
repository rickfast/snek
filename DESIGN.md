# snek design notes

The goal is Python syntax with static semantics. A snek program should read as ordinary typed Python, run unchanged under CPython (with the `snek` shim), and compile to small, fast WasmGC modules without a Python runtime. Where Python's dynamic semantics and a static compiler disagree, snek rejects the program at compile time rather than silently behaving differently. The divergences that remain are listed at the end.

## Modules and imports

Programs can import local `.py` and `.snek` modules and packages. The compiler
links the complete reachable program before checking and emitting a single
Wasm module; there is no runtime Python import system or separate compilation.

- **Root.** Both CLIs accept `--module-root PATH`. The default is the entry
  file's directory, independent of the working directory. Absolute imports are
  relative to this root, not `sys.path`, site-packages, or the ambient filesystem.
  The entry and resolved sources must stay under the root; escaping symlinks
  and multiple logical names for the same source are rejected.
- **Files and packages.** `import geometry.point` can resolve
  `geometry/__init__.py` and `geometry/point.py`, or their `.snek` equivalents.
  Packages require an `__init__` file. If a name has more than one candidate
  (`point.py` and `point.snek`, or a module and a package), resolution fails
  rather than silently choosing one. Standard namespaces (`snek`, `typing`,
  `collections`, `types`, `__future__`) are reserved.
- **Bindings.** `import module`, dotted imports, `as` aliases, named `from`
  imports, re-exports, and package-relative imports work. Functions and classes
  retain separate module namespaces, including in annotations, inheritance,
  closures and defaults. Imports bind static namespaces, not runtime module
  objects. Local variables and parameters can shadow imports. Each module must
  import its own annotation/library names; imports do not leak between files.
- **Initialization.** Imports must be direct module-level statements. An
  imported module's executable statements run once, at its first import, in
  source order; package initializers run before their children. The entry has
  `__name__ == "__main__"`; imported files use their dotted module names.
  Imported `__main__` guards do not run. General import cycles are errors with
  the dependency chain shown. An initializing package can import its children,
  and those children can import siblings through the package.
- **Static limits.** Exports are functions, classes and imported names.
  Module-level variable/constant declarations remain unsupported, as do mutable
  globals, module-attribute mutation, wildcard/conditional/function-local imports,
  conditional module-level function/class declarations, dynamic imports and
  namespace packages. As with single-file Snek, function and
  class declarations are collected before body checking; this is not an
  emulation of Python's dynamic declaration timing. Use functions and explicitly
  passed objects for state. Keep files as `.py` when CPython should import them
  without a custom loader.
- **Implementation.** `bootstrap/modules.py` is Snek code and performs parsing,
  namespace linking and initialization ordering. The reference compiler executes
  it with the shim and adapts the owned tree; the independent compiler executes
  it in Wasm. Project syntax is therefore parsed by the Snek-owned parser; a
  single-file program without local imports retains its existing frontend.
  The Python/Rust launchers only resolve requested paths and supply source text
  through the versioned project envelope. Dependency source locations are
  mapped back into diagnostics. Reading compilation inputs grants no filesystem
  access to the resulting guest. The historical fixed bootstrap-bundle tool
  remains separate and unchanged in purpose.

See `examples/modules/main.py` for a multi-file program that also runs under
CPython. Build it with `python -m snekc run examples/modules/main.py --no-opt`
or `snek run examples/modules/main.py`.

## JavaScript library boundaries

`--library --export FUNCTION` produces a core Wasm library and generated
JavaScript/TypeScript bindings. Both exports and `@host("name")`
imports support fixed-width numbers, booleans and copied strings, plus `None`
returns. Host declarations have typed signatures and `pass` bodies; their host
names remain explicit metadata in checked IR, not ordinary function bodies.
JavaScript supplies the functions when instantiating the library. There is no
ambient DOM, filesystem or network access.

The shared ABI generator lowers strings to UTF-8 pointer/length pairs and copies
them into the GC heap before use. Host string returns use Wasm multi-value
results. Scratch frames are released after each callback, and the JavaScript
entry guard resets the arena on exceptional exit. GC references never escape.
Host string returns, like exported string parameters, enable the Unicode tables
needed for open-world input.

Synchronous host exceptions propagate unchanged to JavaScript, bypassing Snek
exception handlers and cleanup. An `async def` host declaration instead wraps a
Promise as a GC-backed coroutine frame. It starts lazily on first resume, suspends,
and retrieves a checked result on the next resume after JavaScript settles it.
Rejections and invalid resolved values become Snek `RuntimeError`s, allowing normal
exception handling and awaited cleanup. Async exports keep their root frame in a
private GC global until completion; the JavaScript driver awaits pending host work
or yields to the event loop for a cooperative checkpoint. No Wasm stack is retained.

Each instance permits one active exported call, including suspended time. Reentrant
and overlapping calls are rejected, not queued. Scratch memory is reset at suspension
and exit; exported roots are cleared on success and failure. Async initialization,
task spawning, cancellation APIs and object handles remain unsupported. The CPython shim raises on a host call
unless the application replaces the declaration with a Python implementation.
See the [library guide](docs/reference/libraries.md) for the public contract.

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

### Closures and callable values

```python
from typing import Callable
from snek import i32

def counter(start: i32) -> Callable[[], i32]:
    def next_value() -> i32:
        nonlocal start
        start += 1
        return start
    return next_value

def apply(f: Callable[[i32], i32], x: i32) -> i32:
    return f(x)

print(apply(lambda n: n * 2, 5))
```

- **Types.** Import `Callable` from `typing` or `collections.abc`. Parameter and return types must match exactly: no callable variance, dynamic `Callable[..., T]`, `Any` or `ParamSpec`. Callable values can be optional, returned, stored in fields and containers, and compared by identity/equality. They do not support printing, hashing or bound-method conversion yet.
- **Inference.** Nested `def` signatures require annotations, as module functions do. Lambda parameter and result types come from an expected `Callable` annotation on an assignment, argument, return, field or container element. For an inline `sorted`/`min`/`max` key lambda, the element type supplies the parameter and the body determines the result type. Other uncontextualized lambdas are rejected. A fully context-typed closure's capture type can be inferred from a later assignment in its enclosing body; reading its value before that assignment raises `NameError` at runtime. Key lambdas need captured types to be known at the call site; add an annotation for a capture assigned later.
- **Calls.** Calls through function values, including nested function names, are positional and exact-arity. Supply every argument even if the original definition has literal defaults. Direct calls to statically named module functions retain keyword/default binding and the direct-call fast path. Lambda defaults, keyword-only parameters and variadic parameters are unsupported. `lambda: None` and void signatures such as `Callable[[i32], None]` work, including a lambda whose body calls `print`.
- **Representation.** Each callable signature gets a GC struct containing a typed funcref and an environment reference. Its function type takes a hidden environment parameter before the user arguments. Each environment subtype stores shared cell references. A cell contains a mutable, statically typed payload and a bound flag; numbers inside cells remain raw machine values, not tagged Python objects. Only bindings captured by children allocate cells. Module function values use one immutable global object per function, preserving identity.
- **Lexical semantics.** Sibling closures share cells, separate invocations get separate cells, and transitive captures forward the original cells. Nested functions and lambdas escape safely through the GC. Recursive and mutually recursive local functions use the same cells as other bindings. Loop captures are late-bound, not snapshots. `nonlocal` resolves the nearest enclosing function binding and cannot change its type. Shadowing follows lexical scope even before the first assignment.
- **Order and cleanup.** The callee is evaluated once before arguments. Captured-variable reads cannot be moved across calls that might mutate those cells; argument reordering and assignment-target evaluation preserve Python's order. Exception-handler bindings are cleared on every exit, so an escaping closure cannot keep reading an `except ... as e` variable after its handler. Capture cells survive suspension and frame cleanup when a generator/coroutine-created closure still references them.
- **Narrowing.** Shared captures always use their declared type: a callback or resumed generator can mutate them through an otherwise ordinary call. For an optional capture, use `snapshot = value`, then narrow `snapshot` with `is not None` or `isinstance`. This also applies in the owning function when a child captures its local.
- **Remaining limits.** No closures inside `__init__` (whose bodies participate in two-phase initialization and superclass inlining), bound-method or built-in-function values, capture of module-level state, `global`, nested classes, or function reflection. Ordinary methods can return closures capturing `self`. To use a builtin as a key, wrap its call, e.g. `key=lambda x: abs(x)` or `key=lambda s: len(s)`.

## Reference types and None

- `str`, `list[T]`, `dict[K, V]`, fixed-length tuples, `Callable` values and classes are references to GC structs and are non-null by default.
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
- **Class values** use `type[C]` annotations and are references to immutable vtables. `C`, `type(instance)`, identity/equality checks and `.__name__` work. Calling a class-valued variable, reflection, and general metaclasses are not supported.
- **Not yet supported:** properties, static or class methods, and special methods beyond those below.

### Special methods

- `__eq__(self, other: C) -> bool` takes the defining class or one of its bases; `C | None` is also allowed. The checker verifies that every reachable implementation can accept the other operand's static type. A subclass-only method cannot silently receive an incompatible base instance. `NotImplemented`, arbitrary return types, `__ne__`, and comparisons with non-class values other than `None` remain unsupported.
- `==` dispatches with Python's right-subclass priority; otherwise the left method wins, with a reflected right call if only it defines equality. Without a method, equality is identity. `!=` negates that result, and `is` never calls user code. The generated helpers select an implementation from the closed-world dynamic class pair, preserving left-to-right operand evaluation.
- Container equality, membership, `count` and `remove` use the same protocol but first accept identical references, as Python containers do. Direct `obj == obj` still calls `__eq__`. List scans reload lengths and backing arrays after comparisons so a method can resize the list without reading stale storage.
- `__str__(self) -> str` is used by `str`, implicit `print` conversion, empty f-string specs, and `!s`. It supports overrides, `super`, and optional objects. Exception subclasses also use it when reported uncaught; a failure while rendering an uncaught exception produces `<exception str() failed>`. `repr`, `!r`, and container printing do **not** substitute `__str__` for `__repr__`; ordinary object repr remains unsupported.
- `__len__(self) -> i32` (or `i64`) supports `len` and explicit `bool(obj)`. Negative results raise `ValueError`; directly calling `obj.__len__()` returns its result unchanged. The method must be declared on the static receiver type, with the normal override signature rules. Objects without a length protocol are true. There is still no implicit truthiness in conditions and no `__bool__` protocol.
- These methods take no default arguments. Overrides retain the same signatures as ordinary methods. Custom hashing, ordering, arithmetic, `__repr__`, `__format__`, and iterator protocols remain future work; class instances still cannot be dictionary keys.

### Context managers

`__enter__` takes only `self` and may return any supported type. `with ... as target` requires a non-`None` return type. `__exit__` uses this signature (parameter names may differ):

```python
from types import TracebackType

def __exit__(self, exc_type: type[BaseException] | None,
             exc: BaseException | None, tb: TracebackType | None) -> bool:
    return False
```

- `-> None` is also accepted and cannot suppress exceptions. A bool return suppresses only an exception, never a pending return, break or continue. Other return types and implicit truthiness are not supported.
- The manager expression and `__enter__` run once. `as` supports ordinary assignment targets, including tuple unpacking. Failure in `__enter__` does not call that manager's `__exit__`; failure while binding its result does. Multiple managers enter left to right and exit right to left.
- Lowering uses the existing `try`/`except`/`finally` IR and pending-action machinery. A flag ensures `__exit__` runs exactly once, including when it raises. Normal exits pass three `None` values. Exceptional exits pass the actual exception class and instance, plus a non-null opaque traceback token. `exc_type.__name__`, `exc_type is ValueError`, and `isinstance(exc, ValueError)` work after appropriate None checks.
- `TracebackType` is only an annotation import; traceback frames, attributes, identity and equality are not exposed. The token supports None checks, not Python traceback introspection. Bare `raise` remains limited to lexically enclosing `except` blocks; inside `__exit__`, narrow and `raise exc` explicitly when needed.
- A bool-returning `__exit__` might suppress an exception after any prefix of the body. Definite-assignment analysis therefore does not assume body assignments completed or that a return in the body always exits the function. `async with` is not supported yet.

## Exceptions

- **The hierarchy is written in snek** (`snekc/runtime/exceptions.snek`) and declared ahead of every program:
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
  - Dictionary lookup failures store the key's rendered `repr` in a second hidden field. This supports numeric and tuple keys without boxing them or quoting the rendered text twice. Explicit exception constructors still accept only string messages.
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
  - the `args`, `with_traceback` and `add_note` attributes (these names are reserved on exception classes so they can't silently mean something else);
  - exceptions created with more than one argument or with a non-`str` argument.
- **Exception `__init__` must call `super().__init__(message)`.** CPython derives `str(e)` from the constructor arguments when it is skipped, which snek doesn't emulate.

## Strings

- **Code-point semantics, like CPython.** `len(s)`, `s[i]`, slices, `for ch in s`, `find()` results and `ljust()` widths all count code points.
- **Layout.** `(struct $bytes $hash $cp $idx)`:
  - `$bytes` is UTF-8, in one mutable byte-array type that also backs the string builder. Immutability is enforced by the language; a mutable array type can't be a subtype of an immutable one, so separate types would force copies.
  - `$hash` is FNV-1a, computed lazily and cached. Dicts key off it.
  - `$cp` is the code point count, or -1 until something needs it. It equals the byte length exactly when the string is ASCII, so ASCII strings index and slice by byte offset with no extra work.
  - `$idx` is the byte offset of every code point, built the first time a non-ASCII string is indexed or sliced (PyPy uses a similar index). Iteration and the methods walk the bytes and never need it.
- **Searching works on bytes.** UTF-8 is self-synchronizing, so a byte-level match of valid UTF-8 always lands on code point boundaries. `find()` converts the byte offset back to a code point index only when it returns one.
- **Literals** each get a passive data segment and a function that builds the string with `array.new_data` on first use and caches it in a global, so `wasm-opt` drops unused literals with their functions. The code point count is known at compile time.
- **Concatenation and integer f-strings.** Concatenation trees are flattened to avoid repeatedly copying a growing prefix. When a part is a plain `i32`/`i64` decimal conversion, the emitter specializes a helper for the string/integer argument pattern: count byte lengths, allocate the final buffer, then write integer digits directly into it and copy string parts. This avoids intermediate number strings and the array of parts; the common integer f-string allocates only the result byte array and string header (plus one-time cached literals). Expressions and custom `__str__` conversions still run once, left to right; only side-effect-free primitive integer rendering is deferred. Nonempty format specs, floats, booleans and custom conversions retain their existing paths. The shared decimal size/writer helpers cover the full signed i64 range and preserve UTF-8 code-point-length metadata.
- **One-character strings** for ASCII are cached, so `for ch in s` and `s[i]` over ASCII text allocate nothing after the first use of each character.
- **The methods are written in snek** (`snekc/runtime/strings.snek`), on top of byte-level intrinsics (`__dec`, `__bfind`, a string builder, Unicode lookups) that only that code can call. `s.split(",")` compiles to a call of `__str_split(s, ",")`. A method is checked and emitted only if the program uses it. The resource loader resolves private `__unicode_*` identifiers against the Unicode flag ABI without changing comments, string contents, or source line positions.
- **Unicode properties come from a per-program table.** A snek program reads no input, so every code point its strings can hold comes from its own literals, unless it calls `chr()` on a computed value.
  - The compiler embeds the properties of just those code points (closed under the case mappings the program uses), read from the CPython that runs the compiler. For most programs that's a handful of entries.
  - A program that calls `chr(n)` on a computed `n` gets tables covering all of Unicode instead: about 40 KB with every method in use, and a few seconds more compile time.
  - The tables cover printability (for `repr`), the `is…()` predicates, the full case mappings (`"ß".upper() == "SS"`, final sigma in `lower()`), and decimal digit values (`i32("٤٢") == 42`).
- **`repr`** follows CPython's quote choice and escapes: `\\`, `\n`, `\r`, `\t`, and every other non-printable code point as `\xNN`, `\uNNNN` or `\UNNNNNNNN`. It's used by `repr()`, `!r`, list elements and `KeyError`.
- **Lone surrogates can't be stored as UTF-8.** Literals containing them are rejected, and `chr()` of one raises `ValueError`.
- **`i32(s)` and `i64(s)`** parse like `int(s)`, including underscores, Unicode digits and whitespace, with CPython's error messages. A value that doesn't fit raises `OverflowError`. `f64(s)` is rejected for now, because exact float parsing needs big-number arithmetic.

### F-string format specs

- Literal specs support string truncation, code-point width and fill, `<`/`>`/`^`/`=` alignment, signs, zero padding, `#`, comma/underscore grouping, integer `b`/`c`/`d`/`o`/`x`/`X`, and float `e`/`E`/`f`/`F`/`g`/`G`/`%`. `z` suppresses negative zero after rounding. `!s` and `!r` convert before formatting.
- The compiler parses and validates each spec in `snekc/formatting.py`. Helpers in `snekc/runtime/formatting.snek` are checked/emitted on demand, like string methods. Values are evaluated once, left to right.
- Explicit float precision uses the exact binary mantissa and exponent, expanded into decimal digits with base-10⁹ chunks. Decimal rounding is ties-to-even, including carry across the decimal point, subnormals and the largest finite f64. Integer formatting handles the full i64 range without taking the positive magnitude of `INT64_MIN`.
- Empty specs retain `str()` behavior. Float specs without a presentation type or precision also use the existing approximate shortest representation; explicit precision does not share that limitation.
- Dynamic nested specs, locale-aware `n`, `!a`, `str.format()`, and non-empty specs on collections/objects without `!s` or `!r` are not supported. Width and precision must fit in i32; output is still subject to runtime memory limits. `c` shares `chr()`'s surrogate restriction.

## Lists

- `list[T]` is monomorphized: `(struct (field $len (mut i32)) (field $data (mut (ref $arr.T))))`. `list[f64]` stores raw f64s and `list[bool]` uses a packed `(array (mut i8))`.
- **Generated names** use `<`, `>` and `?` (for example `$list.list<Node?>`). Those characters are legal in WAT but not in Python identifiers, so they can't collide with user names.
- **Reference elements** are stored nullable so capacity can be pre-allocated with `array.new_default`. Non-optional element types are read back with `ref.as_non_null`, and `pop` clears the slot so the GC can reclaim it.
- **Growth** doubles the backing array with `array.copy`. Indexing is bounds-checked and supports negative indices.
- **`str()` of a list** prints its elements with `repr`, like CPython. Element types can be numbers, bools, strings, printable collections, or optional references to those.
- **The other operations** (slicing, `in`, `index`, `insert`, `extend`, `+`, `*`, `==`, ...) are generated per element type, only for the operations a program uses. `xs += ys` and `xs *= n` mutate in place, as in CPython.
- **Equality** is `==` for numbers and strings, element-wise for lists, and the class equality protocol for objects (with an identity shortcut). `list.index()` is still rejected for ordinary object elements, because its error message needs their unsupported `repr()`; membership, `count`, `remove`, and tuple `index` do not have that restriction.

### List builtins

- `sorted(xs, *, reverse=False, key=None)` returns a new list, using stable bottom-up merge sort with O(n log n) comparisons and O(n) scratch space. Equal keys retain input order in either direction. Without a key, elements must be numbers, bools or strings. `reverse` must be a bool, following snek's explicit truthiness rules.
- `min(xs, *, default=..., key=None)` and `max(...)` return the first extremum, preserving the original element's identity. An empty list without a default raises `ValueError`. Defaults are evaluated even for non-empty lists, in ordinary call order, and must match the element type; reference defaults may be `None` or an optional reference. The key is never applied to a default. Numeric optionals remain unsupported. The existing two-argument forms accept `key=` too, but not `default=`; they retain the first operand on ties and unordered NaN comparisons.
- **Callable keys.** A module function, typed function value or inline lambda can accept one element and return `i32`, `i64`, `f32`, `f64`, `bool` or `str`. Thus class instances, tuples, collections, optional references and even callables can be sorted or reduced via a supported key. Key parameters must accept the element type without numeric conversion (reference upcasts are allowed). Key return types cannot be optional, tuples, objects or awaitables. Optional key functions must be narrowed first; literal `key=None` retains the no-key path. For inline lambdas, the element type supplies the parameter type and the body infers the key type; `sorted([], key=lambda x: x)` still needs an element annotation, while a named typed key can supply it.
- **Order and mutation.** Source arguments and keyword expressions are evaluated once in source order, before any key callback. `sorted` then copies all input elements and evaluates keys once each, left to right; mutations of the original list during callbacks do not change that copied sequence. Keys live in typed parallel arrays, so merge comparisons do not call the key again or allocate per-element wrapper objects. `min`/`max` instead iterate the live list, reloading its length and storage after each callback, including after appends, removals or growth. They retain only the winning element and its cached key. Callback exceptions propagate immediately, without evaluating further keys; an empty input invokes no key callbacks. Awaited argument expressions are supported, but key callbacks themselves are synchronous.
- `sum(xs, start=0)` accepts numeric/bool lists and a numeric start. Bool elements accumulate as i32; integer sums use the widened integer type and wrap on overflow. i32 elements with an f64 start produce f64; i64-to-float mixing needs an explicit conversion.
- Float lists accumulate in f64 with compensated addition, following CPython 3.13's float fast path. A possibly empty float list requires an explicit float start, e.g. `sum(xs, 0.0)`: Python otherwise returns integer `0` for an empty list, which cannot share a static result type with a non-empty float sum. A non-empty float literal may omit the start. Bool starts are rejected; use `i32(flag)` explicitly.
- These builtins currently accept lists, not arbitrary iterables; `min`/`max` also retain their two-argument forms, not arbitrary variadic forms. Sorted lists with NaN values or NaN keys can differ from CPython's TimSort because NaN comparisons are unordered.

## Dictionaries

- **Types are monomorphized and invariant.** `dict[K, V]` has one fixed key type and one fixed value type. Literals infer those types using the same rules as lists; `{}` and `dict()` need a type annotation or another expected type. `dict(d)` and `d.copy()` make shallow copies.
- **Keys** can be `str`, `bool`, `i32`, `i64`, or fixed-length tuples whose elements are supported keys. Reference keys may be optional, so `dict[str | None, i32]` can contain `None`. Mutable containers and user objects are rejected as keys. Float keys are deferred: unboxed NaNs cannot preserve CPython's identity-based lookup behavior.
- **Lookup uses open addressing with linear probing.** Strings use their cached FNV-1a hash. Integers mix their bits into a 32-bit hash, and tuples combine element hashes. Hashes select a probe sequence; equality checks the actual key. A separate ordered array of entries preserves insertion order through collisions and rehashing.
- **Updating an existing key retains its position and its original key object.** Deletion clears the entry reference and leaves a tombstone in the lookup table. Reinsertion appends a new entry. Table growth rehashes live entries and compacts vacant entries when no iterator is active. During iteration it preserves entry indices; loop cleanup releases the iterator on fallthrough, `break`, `return`, and exceptions.
- **Operations:** literals, `d[key]`, `d[key] = value`, augmented item assignment, `del d[key]`, `len`, `bool`, `in`/`not in`, `==`/`!=`, `get`, `setdefault`, `pop`, `popitem`, `update`, `clear`, and `copy`. `update` accepts another dictionary of the same type. Equality ignores insertion order; `popitem` removes the last inserted live entry. Missing-key lookup, deletion, and `pop` raise catchable `KeyError` with the key's `repr`.
- **Defaults follow the static value type.** `d.get(key)` is supported for reference values and returns an optional reference. Numbers and bools require an explicit default, such as `counts.get(word, 0)`. `setdefault` without a default requires an optional reference value type, because it may insert `None`.
- **Views are live.** `keys()`, `values()`, and `items()` hold the dictionary, so later mutations are visible. They support iteration, length, explicit `bool`, membership, `repr`, and `list(view)`. `list(d)` copies the keys. Item iteration yields `tuple[K, V]`, which can be unpacked in the loop target. Dictionary iteration checks for size changes and, on exhaustion, extra keys; changing existing values is allowed.
- **Evaluation order follows Python.** Key/value expressions in literals run in source order. An assignment evaluates its RHS before its receiver and key; augmented assignment reads the original value before evaluating its RHS. Default arguments are evaluated even when the key is present.
- **Not yet supported:** dictionary comprehensions, `**` unpacking, `|`/`|=`, `fromkeys`, construction/update from iterable pairs or keyword arguments, or set operations/equality on dictionary views. Dictionary values cannot have the bare type `None`; annotate an optional reference type instead.

## Tuples

- **A tuple's length and field types are static.** `tuple[str, i32]` is a two-field immutable WasmGC struct; `tuple[()]` is empty. Each field retains its own type without boxing. Tuples can contain mutable references; the fields themselves cannot be replaced. Tuple types currently require an exact shape and field-type match when assigning existing tuple values.
- **Literals and unpacking** work in assignments, returns, and loop targets, including nested unpacking and swaps. The RHS is evaluated once before assigning targets from left to right. Unpacking requires a fixed-length tuple with exactly as many elements as targets. Starred unpacking and starred literals are not supported yet.
- **Indexing:** literal indices select a field, including negative indices. A statically out-of-range index is a compile error. A non-empty homogeneous tuple also accepts dynamic `i32` indices, which raise `IndexError` when out of range. Heterogeneous tuples must use literal indices or unpacking.
- **Slicing and construction:** slice bounds and repetition counts must be integer literals so the result's shape is known. Concatenation, constant repetition, `tuple()` and `tuple(existing_tuple)` are supported. `list(tuple)` works when its elements share a list element type. `tuple[T, ...]` and construction from a runtime-length iterable are deferred.
- **Equality and printing** recurse through fields; tuples are printed with Python's parentheses, quoting, and singleton comma. Equality currently requires matching tuple types; ordering comparisons are deferred. Non-empty homogeneous tuples support iteration, membership, `count`, and `index`. `len`, `bool`, and `type(t).__name__` still evaluate their argument even when the result is statically known.
- **Optional fields** use optional reference types, such as `tuple[str | None, i32]`. Bare `None` fields are not supported; a `None` literal needs an optional reference annotation. Numbers remain unboxed and cannot be optional.

## Generators and coroutines

- **One representation, two protocols.** A generator returns `Iterator[T]` (also spelled `Generator[T, None, None]`); an `async def ... -> T` call returns `Coroutine[None, None, T]`. The unboxed element/result type is fixed. Both allocate a GC frame without executing the body. Each monomorphized base frame holds a typed resume funcref, program counter, running/started/claimed flags, exception successor and optional result slot; concrete subtypes add parameters, locals and await temporaries. Frame references are cleared on completion or escaping exceptions.
- **Stackless resumption.** `machines.py` extracts awaits into temporaries, preserving Python's operand order and lazy `and`/`or`/conditional branches, then lowers structured IR to a control-flow graph. Each state carries an exception edge. A yield saves locals and the next state and returns; it neither replays prior statements nor retains a Wasm stack. Awaited children propagate pending status to their parents. Ordinary synchronous functions retain the existing structured emitter.
- **Cleanup is part of the graph.** Finally suites are cloned on normal, exceptional, return, break and continue edges. Pending return values and original exceptions are saved separately, so suspension or a caught exception inside cleanup does not overwrite them. Context managers use the existing `try` lowering. Exceptions injected with `throw()` or `close()` enter the handler at the suspension point. `close()` injects `GeneratorExit`; yielding during close raises `RuntimeError`. Closing an unstarted generator executes no body. Escaping `StopIteration` is converted to `RuntimeError` once the body has started (PEP 479).
- **Generator operations:** `for`, `iter(g)` (identity), `next(g)`, `next(g, same_type_default)`, `list(g)`, `g.send(None)`, `g.throw(exception_instance)` and `g.close()`. Iteration catches exhaustion only, never exceptions from the loop body. Exhausted generators stay exhausted; reentrancy raises `ValueError`. Tuples can be yielded and unpacked, references can be optional, and generators can be passed/stored by their annotated iterator type.
- **Coroutine operations:** `await` accepts a typed coroutine and produces its annotated result. `snek.run(coro)` is supported at module scope; `snek.checkpoint()` yields cooperatively once. The CPython shim uses `asyncio.run` and `asyncio.sleep(0)`. A coroutine can be awaited only once. Async methods and `super()` share ordinary virtual dispatch. Core modules drive the root frame to completion; components return to the host at scheduling points.
- **Explicit limits:** no sent values other than `None`, yield expressions, `yield from`, generator return values, asynchronous generators/iteration/context managers, task spawning, arbitrary awaitables or general `asyncio` imports. Async timers are supported through `snek.time.sleep`. `Iterator[T]` currently denotes snek generators, not arbitrary user-defined iterator protocols. Unawaited-coroutine warnings and GC-triggered generator finalization are not emulated. Close unfinished generators explicitly, especially if a suspended `with` or `finally` owns resources. Breaking a loop does not itself close its iterator.

## The WASI boundary

- **Linear memory is used only for I/O.** WASI preview1's `fd_write` takes a pointer and length, so the module starts with one page of linear memory. Host metadata and random bindings can grow an inbound-data arena above that page.
- **The copy is a byte loop.** There is no bulk GC-array↔memory instruction, so stdout is buffered there and flushed once every 63 KiB, at exit, and before an uncaught exception is reported.
- **Core byte streams.** `read_bytes() -> list[i32]` and `write_bytes(data: list[i32]) -> None`, imported from `snek`, expose binary stdin/stdout without filesystem access. Reads continue through short reads to EOF; writes handle short writes and reject zero progress. The entire output list is validated as `0..255` before any of its bytes are written. Both operations flush pending text output before reusing the WASI scratch page. Host I/O failures raise `RuntimeError`; byte-range failures raise `ValueError`. These older synchronous APIs remain core-only; components use the standard-library streams below. `runner.run_binary` and the CLI preserve non-UTF-8 output; the text testing wrapper decodes with replacement.
- **Native components are opt-in.** `build --component` emits a `wasi:cli/run@0.3.0` async command and a standalone `.wit` describing its exact imports/exports. `snekc/wit/` pins the small WASI CLI subset used. Snek encodes the private core module, with optional Binaryen optimization; wasm-tools assembles the component, validates it with `cm-async`, and checks conformance against the WIT world. There is no preview1 adapter or preview1 import in the component. The default core target remains available for Node and Python Wasmtime.
- **Callback-driven output.** Print buffers become ordered GC-owned chunks until the next cooperative checkpoint or command completion. The component runtime transfers them through WASI 0.3 `stream<u8>` and `future<result<_, error-code>>`. It handles partial writes and returns the packed `WAIT` callback code while a write/future is pending. It never overwrites borrowed linear memory until completion is delivered, and drops stream, future and waitable-set handles. `YIELD` returns control for a coroutine checkpoint. Synchronous code that prints without checkpoints can accumulate its output in the GC queue; this is a memory tradeoff of this initial stackless output bridge.
- **Command lifetime.** One invocation per component instance may be active at a time; an overlapping invocation traps rather than replacing its live frame. Sequential invocations reset the command state. Host cancellation is cooperative: outstanding output finishes before `GeneratorExit` is injected through the awaited frame chain, allowing finalizers to suspend; cancellation is acknowledged after cleanup. This can wait for a blocked output sink. Uncaught exceptions are printed to stderr and exit nonzero after queued output drains.
- **GC stays private for now.** The stable WASI 0.3 boundary uses linear memory; strings/bytes are copied from GC arrays. Wasmtime 49 exposes an experimental component-GC switch but [documents its support as very incomplete](https://github.com/bytecodealliance/wasmtime/blob/v49.0.2/crates/wasmtime/src/config.rs#L1344). Enabling that switch alone is not an interoperable GC canonical ABI. Adoption remains a separate roadmap follow-up; this output requires no experimental GC ABI option.

### Standard-library CLI streams

`snek.sys` is the first library facade over the WASI interfaces. Imports bind
static namespaces separately from user classes/functions. The checker maps
stream operations to typed IR calls; an ordinary local with the same name takes
lexical precedence. Module aliases and direct stream imports are supported;
streams are first-class values with `snek.io` type annotations. Local source
modules use the static linker described above. The CPython shim supplies the same source API for
differential tests.

Text writes accept `str`, encode UTF-8 and return a code-point count. Binary
writes accept `list[i32]`, validate every value before submitting output, and
return a byte count. Output chunks own their bytes. `flush()` submits pending
text; `await drain()` yields to the scheduler until the queued streams and
their terminal futures complete. The host's `io`, `illegal-byte-sequence` and
`pipe` results become `OSError`, `OSError` and `BrokenPipeError`; a premature
reader drop also produces a broken-pipe error. Errors are tracked independently
for stdout/stderr and consumed when that stream is flushed/drained. Existing
`print()` retains its quiet output-error behavior.

`await stdin.buffer.read()` lowers to the pinned
`wasi:cli/stdin@0.3.0` stream-plus-future interface. Its import and canonical
read/cancel/drop builtins appear only when the program uses stdin; the
delivered WIT world describes that exact import set. The input pump handles
partial transfers, copies completed bytes into a GC list, waits for the
terminal error future at EOF, and drops both readable handles at EOF, close,
cancellation, or command completion. `read(size=-1)` fills a bounded request
without waiting for EOF (negative sizes still read everything). `readline`
includes a newline and retains any following bytes as GC lookahead for the next
call. Zero-sized requests do not acquire stdin. Requests serialize over one
retained session; idle sessions hold no borrowed scratch memory. Text decoding
is a separate follow-up. Preview1 line reads are byte-wise to avoid reading past
the newline without a canonical stream buffer.

`StreamT` distinguishes text/binary and input/output views at compile time. GC
stream objects reference a shared resource state containing the descriptor and
closed flag; text streams retain a distinct binary view of that state. Aliases,
fields, function arguments, optional types and collections use ordinary
reference storage. `close()` closes all views idempotently and releases stdin's
retained handles, but never closes a host-owned standard-I/O descriptor. Output
close submits buffered data; callers drain before close to observe async host
errors. `with` lowers to an open check and try/finally close, including coroutine
unwinding. Each command invocation resets the singleton views. Bound methods
and user-created stream resources remain unsupported.

Input and output share the scratch page and run sequentially. A suspended read
retains the page until its callback arrives. Cancellation detaches a pending
read/future from the waitable set and uses WASI 0.3's synchronous cancel builtin
to reclaim that borrow before injecting `GeneratorExit`. Cleanup may suspend
and perform new I/O. Completed requests release their result buffers. Handles
are dropped at EOF; any unread lookahead remains until consumed. Close,
cancellation and command completion release both handles and lookahead,
including across repeated invocations. Core modules expose the same
coroutine API over blocking preview1 input/output.

Future library packages should retain this split: language utilities operate
on Snek values; host operations lower to versioned WIT interfaces. Filesystem
operations will use explicit directory capabilities granted by the host.
HTTP and sockets will require their own imports. Public component contracts
will use portable WIT values independently of the Python-shaped source API.

### Host configuration, clocks and randomness

The library namespaces `snek.os`, `snek.time` and `snek.secrets` reuse the static
namespace resolution used by `snek.sys`. `sys.argv` is an ordinary typed GC
list, initialized lazily and reset between command invocations; list mutation
and methods work normally. `os.getenv` reads only the environment the host
supplies. Its result is optional unless the default is statically a string.
Arguments and defaults retain source evaluation order, including awaited
defaults. The runner and CLI grant environment variables explicitly, rather
than passing their full host environment to the guest.

`wasi_bindings.py` selects the pinned 0.3.0 interface imports and canonical
lowering options from the library operations used by the compiled program.
The delivered WIT contains the same selected interfaces. Primitive clocks
cross directly as integers or the system clock's signed-seconds/nanoseconds
record; language-facing queries return `f64` seconds. A signed system instant
preserves dates before the Unix epoch. Monotonic marks remain unsigned at the
boundary. The compatibility target uses preview1 clock, random, args and
environment imports.

Variable-sized canonical strings/lists need more space than the CLI scratch
page. `abi_memory.wat` provides an aligned temporary allocator with memory
growth, preserving old allocations when canonical realloc requests it. In a
component, the allocator belongs to the separate core memory instance so
lowered imports can use it without an instantiation cycle. Core modules
embed the same allocator. Allocations start at byte 65536, never overlapping
the borrowed CLI buffer. Host return values are copied into GC arrays/strings
before resetting the arena; memory retains its high-water allocation. POSIX
pointer tables are converted to the canonical list layout, with bounded
NUL scans and UTF-8 validation. The arena is for synchronous inbound values;
future bindings retaining pointers across async calls will need explicit
allocation lifetimes.

Async sleep lowers to a WASI monotonic-clock subtask. A requested timer starts
after queued output/input completes, avoiding competing events in the current
sequential scheduler. The dispatcher handles STARTING/STARTED progress and
terminal events, drops the subtask handle, and resumes the coroutine. Command
cancellation detaches and synchronously cancels the pending subtask before
injecting `GeneratorExit`. Cleanup can await a new timer. Core sleep uses one
relative monotonic clock subscription in `poll_oneoff`. Duration conversion
validates negatives, NaN and overflow, then rounds positive fractional
nanoseconds up.

Secure token generation requests at most 64512 bytes per host call and fills
permitted short reads until the requested count is satisfied. Zero progress
and oversized results raise `OSError`. `randbelow` rejects the incomplete
tail of the 32-bit sample space before taking a modulus, keeping the result
uniform. No deterministic generator substitutes for host entropy. Byte
tokens remain `list[i32]` until a dedicated bytes type is implemented.

## Native binary encoding

`snekc/wasm.py` encodes core modules in pure Python. The existing folded WAT is its input, so generated functions and the hand-written runtimes use the same instruction path, and `wat` / `--keep-wat` stay useful for inspection. It is deliberately a compiler-owned dialect, not a general WAT assembler: unsupported forms or instructions are errors, not an implicit fallback to an external tool.

The encoder resolves module index spaces and recursive GC type groups before emitting bodies. It preserves explicit function type uses (including their recursion-group identity), interns implicit signatures outside those groups, resolves named fields/locals/labels, and writes LEB128 immediates and little-endian numeric constants. It handles Snek's typed references, packed arrays, `call_ref`, `try_table`, declarative function elements, passive data, and `memory.size`/`memory.grow`. Catch targets are resolved outside the `try_table` label; branches in its body include that label. Tag and data-count sections use their required section ordering. Raw modules include function, local, type, field and other symbol names for debugging.

`build --no-opt` needs only Python: no Binaryen, runtime package, or `wasm-tools`. Default builds feed the native binary to `wasm-opt -O3 --closed-world`, retaining optimization and dead-runtime elimination. Raw output is larger because it retains the runtime and debug names. `--component --no-opt` also bypasses Binaryen, but component packaging, validation and WIT checking still require `wasm-tools`; this phase does not implement a component binary encoder.

This is encoding, not an independent Wasm type validator. The Snek checker/emitter establish language and lowering invariants; engines validate the emitted core binaries, and Binaryen validates optimized builds. Tests run raw and optimized programs against CPython on Wasmtime and Node, exercise native component output, and check encoder edge cases in `tests/check_wasm.py`. When `wasm-tools` is available, that script also compares numeric/memory encodings against its independent assembler and validates focused GC/EH fixtures.

Finite float constants are emitted in hexadecimal to preserve the IR's exact
value. The binary encoders round textual `f32` literals directly to single
precision: parsing through `f64` first can double-round values near a halfway
point. Bootstrap parity tests cover those boundaries as well as signed zero,
subnormals and NaN payloads.

## Self-hosting bootstrap

The binary encoder has a second implementation in `bootstrap/encode.py`, written
entirely in Snek. It reads WAT as byte values, resolves an owned tree of typed
nodes, and writes a core Wasm binary. Decimal/hex float literals are rounded
using integer limbs, so the compiled encoder needs no host numeric parser. It
omits debug names; the Python encoder's `debug_names=False` mode supplies an
exact seed comparison. The production build path remains the Python compiler.

`bootstrap/bootstrap.py` builds that seed once with the Python frontend, then
runs the Wasm encoder twice over its own generated WAT. All three binaries must
match exactly, and the final encoder must produce an independently runnable
program. It can additionally compare every core/component-core example binary.
Artifacts are published only after all requested checks pass.

The source frontend now also has a standalone Snek implementation in
`bootstrap/parser.py`: UTF-8 decoding, logical-line/indentation tokenization,
recursive-descent statements, precedence-based expressions, string/f-string
readers, and an owned tagged AST. Numbers retain source spelling for later typed
conversion; nodes have named fields and source origins, without CPython object
dependencies. Its core WASI command reads source and prints the AST as JSON.
Differential tests compare both bootstrap sources, the language/error/benchmark
fixtures, built-in source, grammar cases and malformed inputs against CPython,
then compare the shim and compiled Wasm outputs. The Snek binary encoder can
assemble this parser too. Named Unicode escapes use a checked-in Unicode 15.1
table, alias data, and algorithmic CJK/Hangul lookup, generated reproducibly
without network access by `bootstrap/generate_unicode_names.py`.

`--frontend snek` (or `SNEK_FRONTEND=snek`) integrates this parser into the
compiler. CPython remains the default. During this transition the Snek code
runs on the Python shim; `snekc/frontend.py` adapts its owned tree to the Python
checker's AST interface, including assignment contexts and literal conversion.
Built-in source and quoted type annotations use the same selected parser, with
no `ast.parse` fallback. Tests compare exact core/component-core WAT and checker
errors, and also feed ASTs from the compiled Wasm parser through the pipeline.
The adapter is temporary and will disappear when the checker consumes owned
nodes directly. Source locations are diagnostic origins with zero-width end
spans, not a lossless reproduction of CPython's concrete syntax positions.

The first checker slice, `bootstrap/declarations.py`, now consumes those owned
nodes directly. Its Snek-owned records resolve imports, types, signatures,
class hierarchies, fields, constructor signatures and virtual-method slots.
The build-time bundle supplies built-in source and namespace registries as data,
and reuses the encoder's exact numeric helpers for default comparison. Compiled
Wasm and shim outputs are compared against the Python declaration pass across
the source corpus, including the new pass's own bundle. The JSON declaration IR
is versioned and explicitly labels body checking as pending; function bodies
remain syntax trees at that stage.

`bootstrap/bodies.py` adds an experimental body pass over those records.
It owns typed expression/statement trees, resolves local scopes and scope-wide
annotations, tracks definite assignment and optional-reference narrowing, and
lowers numeric/string operations, direct calls, branches, loops, printing and
simple raises/asserts. Argument order, short-circuiting and lazy assertion
messages remain explicit in the IR. Unsupported features are rejected, without
Python-checker fallback. Raw/optimized Wasm output is compared to the shim and
the existing compiler, including executable Fibonacci/Mandelbrot programs.
The `language-v6` IR also covers typed lists, tuples, dictionaries and live
views, mutation and iteration, and ordinary classes with two-phase constructors,
inlined superclass initialization, field access, virtual methods and checked
object protocols. Dictionary loops lower their cleanup explicitly, including
early returns. Class layouts, method slots and override entry casts come from
owned declaration records; the bridge never rechecks these bodies in Python.

String methods and integer parsing now enqueue the needed Snek runtime helpers
and transitively check their bodies through this same pass. Fixed helper source
and intrinsic signatures are build-time inputs; private buffer types and
intrinsics are available only while checking those helpers. IR records Unicode
property, case-mapping and decimal-digit requirements. Source exception handling
lowers typed/tuple/bare handlers, re-raises, `else` and `finally`, with conservative
definite assignment and handler-binding deletion. User exception constructors
and string overrides use the existing class machinery. Tests cover nested
cleanup, nonlocal exits, helper privacy and disabled Python-checker fallbacks.

Unkeyed list sorting and reductions (`sorted`, `sum`, `min` and `max`) now produce
resolved `ListOp` IR directly, including result widening, optional defaults,
empty-list inference and source-order captures. The existing emitter provides
stable sorting and compensated float sums; no Python builtin checker is called.
Callable keys use the same source-order captures and resolved keyed-list IR.
Imported binary `read_bytes`/`write_bytes` now resolve to the existing core WASI
runtime with checked `list[i32]` signatures; component output rejects them.

The owned checker also handles static f-string formatting, tuple operations,
constructor returns, class objects and context managers. Format validation is
Snek code, and formatting helper bodies enter the ordinary demand-check queue.
Lexical scopes are analyzed by `bootstrap/scopes.py`; deferred nested bodies,
shared captured cells, environments and exception-binding cleanup are lowered
in Snek, without calling Python closure conversion. Generator/async functions
carry explicit suspension metadata and operations, and static standard-library
names resolve to typed runtime calls. CFG/frame generation remains an emitter
responsibility and is not part of the checker self-hosting claim.

`bootstrap/body_bridge.py` adapts this resolved IR to the existing emitter and
supplies the checked current exception-allocation ABI. That bridge, emission,
Unicode property data and binary encoding of resulting programs remain Python
in this experimental path. A runtime fingerprint guards helper source, intrinsic
signatures and the Unicode-use ABI against stale checker artifacts. The body
pass now checks its unchanged full source bundle, including the command's binary
input boundary. `bootstrap/bootstrap_checker.py` runs that source through a seed
checker, emits stage 2 through the Python bridge, then repeats with stage 2 to
produce stage 3. Those rebuilt binaries must match exactly. Stage 3 must also
check itself, every example and errors, and produce IR for an executable
smoke program. A hash manifest and the rebuilt checker are published only after
verification. Tests run the body suites against stage 3 and separately optimize
it, with Python checking/parsing disabled during the rebuild proof.

Those pass-level checks are retained independently of the full compiler proof.
Both reference-compiler frontend choices still use the Python checker/emitter;
`--compiler snek` instead runs the independent Wasm compiler.
`bootstrap/emit_support.py` implements type/layout/literal generation
and Unicode table generation in Snek, with exact-output probes in raw and
optimized Wasm. Unicode properties are inherited from the seed's embedded
runtime, without host Unicode calls during compilation. `bootstrap/machine_lower.py`
performs suspension CFG lowering in compiled Snek. `SNEK_MACHINE_LOWERER` selects
that executable through a lossless checked-IR wire adapter, including for runtime
helper machines; failure never falls back to Python CFG construction.
`bootstrap/machine_emit.py`, selected with `SNEK_MACHINE_EMITTER`, generates
frame layouts/factories, local save/restore, resume/exception dispatch and machine
protocol helpers in Snek. Its host adapter supplies resolved Wasm types, literal
references and ordinary expression/statement fragments. Exact graph/WAT parity
and executable core/component tests guard both boundaries with Python suspension
emission disabled. The adapter invokes Wasm separately per graph and frame,
plus module-wide layouts/helpers. Neither selection changes the remaining
Python expression emitter or module/runtime assembler.
The complete implementation connects those passes directly inside Snek.
`emit_program.py` emits owned typed expressions, statements and ordinary
functions. `emit_runtime.py` specializes collections, keyed operations and
binary I/O; `emit_host.py` generates WASI adapters. `emit_module.py` assembles
types, closed-world protocol dispatch, closures, runtime code and entry points.
`emit_suspension.py` maps owned bodies to the generic CFG pass and supplies
ordinary code fragments to frame emission without a Python IR/wire adapter.
The owned encoder produces the final core binary.

`bootstrap/bootstrap_compiler.py` prepares a fixed source bundle and builds a
seed with the Python shim/reference emitter. Then the seed compiles that source
to stage 2, and stage 2 compiles it to stage 3. Host parser, checker, bridge,
emitter and encoder entry points are disabled for both rebuilds. Stage 2 and
stage 3 must be byte-identical; stage 3 must compile and run the smoke program
and reject invalid source without partial output. Publication follows these
checks. `check_selfhost_compiler.py` verifies the source/artifact hashes and
fail-closed driver integration. The full differential suite runs through
`SNEK_COMPILER=snek`; `check_selfhost_components.py` reruns the stream/host ABI
suites with host compiler passes disabled, including cancellation and reset.

The compiler binary accepts source on stdin, emits core Wasm on stdout, and
supports `--wat`, `--component` (component-core target), and `--encode` (WAT
input). The existing CLI remains an orchestrator; Binaryen can optimize the
result and the host component linker plus `wasm-tools` can package it. Neither
is part of the source-to-core fixed point. The Python compiler remains the
default/reference implementation. See [`bootstrap/README.md`](bootstrap/README.md)
for reproducible proof and regression commands.

## Engines

- **Wasmtime 47+** runs core modules with its defaults; GC and exception handling are both on. The component target is tested with the **Wasmtime CLI 49.0.2** and **wasm-tools 1.250.0**. The Python Wasmtime runner is core-only.
- **Node 24+** (V8 13.x) also works. Node 22's V8 12.4 mis-executes a `try_table` that produces a value. `wasm-opt -O2` and above generate exactly that when they turn a caught `throw` into a branch, so the runner refuses Node versions before 24.

## Known divergences from CPython

These are accepted on purpose or still pending:

- **Integer width.** Arithmetic wraps at 32 or 64 bits, while CPython integers are unbounded. Float→int conversions that don't fit raise `OverflowError` instead of producing a big int.
- **`chr()` of a surrogate** raises `ValueError`. CPython returns a string that can't be printed or encoded as UTF-8.
- **NaN in collections.** Element comparisons use numeric `==`. CPython checks object identity first, so the same NaN object can compare equal to itself inside a list, tuple, or dictionary value. Float dictionary keys are rejected for this reason.
- **Float repr.** `str(float)` uses the shortest of 15, 16 or 17 significant digits that round-trips. That matches CPython for ordinary values; a full Ryu or Grisu port would make it exact.
- **Float `%`** uses `a - b*trunc(a/b)`, because Wasm has no fmod. It can differ for very large magnitudes.
- **`f32` values** print as their f64 expansion.
- **Traps.** Stack overflow is a Wasm trap rather than a catchable `RecursionError`. A trap also loses whatever stdout is still buffered.
- **Exception diagnostics.** Uncaught exceptions report their class and message, not a Python traceback or CPython's optional spelling suggestions for missing names. Caught closure `NameError` messages match CPython's `str(exc)`.
