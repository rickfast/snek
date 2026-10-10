# snek

A statically typed subset of Python that compiles to WebAssembly using WasmGC, targeting WASI.

```python
from snek import i32, f64


class Shape:
    def area(self) -> f64:
        return 0.0


class Circle(Shape):
    r: f64

    def __init__(self, r: f64) -> None:
        self.r = r

    def area(self) -> f64:
        return 3.0 * self.r * self.r


def total(shapes: list[Shape]) -> f64:
    t = 0.0                       # inferred f64
    for s in shapes:
        t += s.area()             # call_ref through a vtable struct
    return t


print(total([Circle(1.0), Circle(2.0)]))
```

Every snek program is also a valid Python program. With the `snek` shim on the path it runs under CPython, which is how the test suite checks the compiler.

## Quick start

```sh
pip install wasmtime        # runtime (alternatives: the wasmtime CLI >= 47, or Node 22+)
npm i -g binaryen           # wasm-opt assembles and optimizes the WAT (or brew/apt install binaryen)

python -m snekc run   examples/shapes.py
python -m snekc wat   examples/shapes.py     # print the generated WAT
python -m snekc build examples/shapes.py     # writes shapes.wasm (3.6 KB at -O3)
python -m snekc check examples/shapes.py
python tests/run_tests.py
```

If `wasm-opt` isn't on your PATH, set `SNEK_WASM_OPT` to the command (for example `SNEK_WASM_OPT="npx wasm-opt"`).

Tested with Python 3.13, Binaryen 132, Wasmtime 49 (via the `wasmtime` pip package) and Node 22.

## Pipeline

```
prog.py
  → ast.parse                          CPython's own parser
  → check.py   declare pass            classes, fields, vtable slots, signatures
               check/elaborate pass    inference, narrowing, definite assignment
  → ir.py      typed IR                every type resolved, every conversion explicit,
                                       for-loops / f-strings / __init__ already lowered
  → emit_wat.py + prelude.wat          WasmGC types, vtables, monomorphized lists
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
| `snekc/emit_wat.py` | IR to WAT: type section, vtables, per-element-type list runtime |
| `snekc/prelude.wat` | Hand-written runtime: strings, number formatting, Python division, WASI I/O |
| `snekc/driver.py`, `runner.py`, `__main__.py` | Build pipeline, Wasmtime/Node runner, CLI |
| `snek/__init__.py` | CPython shim (`i32 = int`, …) so programs run under CPython |
| `examples/` | Programs that double as differential tests |
| `tests/errors/` | Programs that must be rejected, each with its expected error |
| `DESIGN.md` | The language rules and the Wasm mapping, with reasons |

## What works

- **Machine numerics.** `i32`, `i64`, `f32`, `f64` and `bool`, with Python's `//` and `%` semantics. Only lossless widening is implicit. Integer overflow wraps.
- **Classes.** Reference types only, single inheritance, virtual methods and `Final` fields.
  - Closed-world compilation emits leaf classes as `final` and devirtualizes calls when only one implementation is reachable.
  - `__init__` follows a definite-initialization rule, so each object is built with a single `struct.new`.
- **Optionals and narrowing.** `T | None` works for reference types. Locals narrow after `is None` and `is not None` checks, `isinstance`, `assert`, early returns, and inside `and`/`or` and conditional expressions.
- **Strings.** UTF-8 `str` with literals, concatenation, comparison, f-strings, and `str()` of numbers, bools and lists of numbers.
- **Lists.** `list[T]` is monomorphized per element type. It supports literals, indexing (including negative indices), `append`, `pop`, `len`, iteration and nesting. `list[bool]` uses a packed i8 array.
- **Control flow.** `if`/`elif`/`else`, `while`, `for` over `range()` and lists, `break`, `continue`, `assert`, and recursion.
- **Runtime errors.** `IndexError` and `ZeroDivisionError` flush stdout, print to stderr and exit with status 1.

## Roadmap, roughly in order

1. **Exceptions.** `try`/`except`/`raise` on Wasm exception handling (on by default in Wasmtime 47+), replacing the panics.
2. **`dict[K, V]` and tuples.** Open addressing keyed off the cached string hash; tuples as immutable structs.
3. **`super().__init__(...)` and `super().method()`.** Base-class calls become direct calls to the base implementation.
4. **Strings.** Settle code-point versus byte indexing, then add iteration, slicing and methods.
5. **Generators and `async def`.** Lower both to state machines; `async def` targets WASI 0.3's callback-style async ABI.
6. **Component model output.** Emit a component with WIT instead of preview1 imports, and adopt the GC canonical ABI once runtimes support it.
7. **Closures and lambdas.** A struct pairing a funcref with its captured environment.
8. **Own binary encoder.** Keep wasm-opt only as an optimizer.
9. **Self-hosting.** Rewrite `snekc` in snek.
