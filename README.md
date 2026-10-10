<p align="center">
  <img src="docs/logo.png" alt="A blue snake and a yellow snake playing in a wooden sandbox with a bucket and spade" width="320">
</p>

# snek

A statically typed subset of Python that compiles to WebAssembly, using WasmGC for objects and Wasm exception handling for `try`/`except`, and targeting WASI.

```python
from snek import i32, f64


class Shape:
    def area(self) -> f64:
        return 0.0


class Circle(Shape):
    r: f64

    def __init__(self, r: f64) -> None:
        if r < 0.0:
            raise ValueError(f"negative radius {r}")
        self.r = r

    def area(self) -> f64:
        return 3.0 * self.r * self.r


def total(shapes: list[Shape]) -> f64:
    t = 0.0                       # inferred f64
    for s in shapes:
        t += s.area()             # call_ref through a vtable struct
    return t


try:
    print(total([Circle(1.0), Circle(-2.0)]))
except ValueError as e:           # a try_table plus a ref.test on the caught object
    print("bad shape:", e)
```

Every snek program is also a valid Python program. With the `snek` shim on the path it runs under CPython, which is how the test suite checks the compiler.

## Quick start

```sh
pip install wasmtime        # runtime (alternatives: the wasmtime CLI >= 47, or Node 24+)
npm i -g binaryen           # wasm-opt assembles and optimizes the WAT (or brew/apt install binaryen)

python -m snekc run   examples/shapes.py
python -m snekc wat   examples/shapes.py     # print the generated WAT
python -m snekc build examples/shapes.py     # writes shapes.wasm
python -m snekc check examples/shapes.py
python tests/run_tests.py
```

- If `wasm-opt` isn't on your PATH, set `SNEK_WASM_OPT` to the command (for example `SNEK_WASM_OPT="npx wasm-opt"`).
- `SNEK_RUNTIME=wasmtime-py|wasmtime|node` picks the engine, and `SNEK_NODE` points at a specific Node binary.

Tested with Python 3.13, Binaryen 132, Wasmtime 49 (via the `wasmtime` pip package) and Node 24. Node 22 is refused, for the reason given in DESIGN.md under "Engines".

## Pipeline

```
prog.py
  → ast.parse                          CPython's own parser
  → check.py   declare pass            builtins.py exceptions, then classes, fields, vtable slots,
                                       signatures
               check/elaborate pass    inference, narrowing, definite assignment, argument binding
  → ir.py      typed IR                every type resolved, every conversion explicit; for-loops,
                                       f-strings, __init__ and super().__init__ already lowered
  → emit_wat.py + prelude.wat          WasmGC types, vtables, monomorphized lists, try_table
  → wasm-opt                           assemble + -O3 --closed-world
  → prog.wasm                          imports only wasi_snapshot_preview1
```

The IR is the seam. A binary encoder, a component-model back end, or a Rust port would all consume it unchanged.

## Layout

| Path | What it is |
|---|---|
| `snekc/check.py` | Declaration pass and type checker; produces the IR |
| `snekc/ir.py` | The typed IR |
| `snekc/types.py` | Type representations, widening and assignability rules |
| `snekc/builtins.py` | The exception hierarchy, written in snek |
| `snekc/emit_wat.py` | IR to WAT: type section, vtables, per-element-type list runtime, `try_table` and `finally` lowering |
| `snekc/prelude.wat` | Hand-written runtime: strings and `repr`, number formatting, Python division, checked conversions, exception reporting, WASI I/O |
| `snekc/driver.py`, `runner.py`, `__main__.py` | Build pipeline, Wasmtime/Node runner, CLI |
| `snek/__init__.py` | CPython shim (`i32 = int`, …) so programs run under CPython |
| `examples/` | Programs that double as differential tests |
| `tests/errors/` | Programs that must be rejected, each with its expected error |
| `DESIGN.md` | The language rules and the Wasm mapping, with reasons |
| `bench/` | Benchmarks against Pyodide, py2wasm, CPython WASI, RustPython, MicroPython and native CPython |

## What works

- **Machine numerics.** `i32`, `i64`, `f32`, `f64` and `bool`, with Python's `//` and `%` semantics.
  - Integer overflow wraps.
  - Float→int conversions raise like `int()`.
  - Ints mix into floats only where CPython's result is a float too.
- **Classes.** Reference types only, single inheritance, virtual methods and `Final` fields.
  - `super().__init__(...)` and `super().method(...)` work.
  - Closed-world compilation emits leaf classes as `final` and devirtualizes calls when only one implementation is reachable.
  - Objects are built with a single `struct.new`, using Swift-style two-phase initialization.
- **Exceptions.**
  - `try`/`except`/`else`/`finally`, `raise`, bare `raise`, tuples of classes and `as e`.
  - The built-in hierarchy plus user exception classes, with `str(e)` and `type(e).__name__`.
  - Runtime errors such as `IndexError` and `ZeroDivisionError` are catchable, with CPython's messages.
  - An uncaught exception reports CPython's last traceback line and exits 1.
- **Functions.** Default values (literals) and keyword arguments, with CPython's evaluation order.
- **Optionals and narrowing.** `T | None` works for reference types. Locals narrow after `is None` and `is not None` checks, `isinstance`, `assert`, early exits, `except ... as`, and inside `and`/`or` and conditional expressions.
- **Strings.** UTF-8 `str` with literals, concatenation, comparison, f-strings, and `str()` of numbers, bools, exceptions and lists. List elements print with `repr`.
- **Lists.** `list[T]` is monomorphized per element type. It supports literals, indexing (including negative indices), `append`, `pop`, `len`, iteration and nesting. `list[bool]` uses a packed i8 array.
- **Control flow.** `if`/`elif`/`else`, `while`, `for` over `range()` and lists, `break`, `continue`, `assert`, and recursion.

## Performance

The same six programs run on every runtime: calls, float loops, allocation, lists, strings and exceptions. Times are medians in milliseconds; see [`bench/`](bench/README.md) for all eight runtimes, startup numbers and caveats.

| | fib | mandelbrot | binary_trees | sieve | strings | exceptions |
|---|---|---|---|---|---|---|
| **snek (V8)** | **5** | **7** | **37** | **13** | **99** | **7** |
| **snek (Wasmtime)** | **8** | **7** | 502 | 24 | 898 | 39 |
| CPython 3.13, native | 102 | 205 | 713 | 122 | 205 | 231 |
| Pyodide (V8) | 160 | 320 | 1,172 | 157 | 489 | 589 |
| CPython 3.12 WASI build (Wasmtime) | 516 | 1,409 | 7,698 | 673 | 1,858 | 3,399 |

- snek modules are 0.3–2 KB and start in about a millisecond. Pyodide takes 2.2 s to load, and the CPython WASI build is 26 MB.
- Allocation-heavy code is 10–15x slower on Wasmtime than on V8, because of Wasmtime's young garbage collector.

## Roadmap, roughly in order

1. **`dict[K, V]` and tuples.** Open addressing keyed off the cached string hash; tuples as immutable structs. `KeyError` is ready for it.
2. **Strings.** Settle code-point versus byte indexing, then add iteration, slicing and methods.
3. **Special methods.** `__eq__`, `__str__`, `__len__`, then `with` statements via `__enter__`/`__exit__`.
4. **Generators and `async def`.** Lower both to state machines; `async def` targets WASI 0.3's callback-style async ABI.
5. **Component model output.** Emit a component with WIT instead of preview1 imports, and adopt the GC canonical ABI once runtimes support it.
6. **Closures and lambdas.** A struct pairing a funcref with its captured environment.
7. **Own binary encoder.** Keep wasm-opt only as an optimizer.
8. **Self-hosting.** Rewrite `snekc` in snek.
