<p align="center">
  <img src="docs/logo.png" alt="A blue snake and a yellow snake playing in a wooden sandbox with a bucket and spade" width="320">
</p>

# snek

A statically typed subset of Python that compiles to WebAssembly, using WasmGC for objects and Wasm exception handling for `try`/`except`, and targeting WASI.

**[Documentation](https://rickfast.github.io/snek/)** ·
[Getting started](https://rickfast.github.io/snek/learn/installation/) ·
[Language reference](https://rickfast.github.io/snek/language/) ·
[CLI reference](https://rickfast.github.io/snek/reference/cli/)

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

### Standalone executable and package-manager builds

The standalone `snek` executable embeds the self-hosted compiler and Wasmtime.
It supports `snek build`, `snek run`, `snek check` and `snek wat` without an
external runtime. npm (`npx`/`bunx`) and PyPI (`uvx`) distributions use the name
`snek-lang`, with both `snek` and `snek-lang` commands.

**The packages are not published yet.** See [packaging/README.md](packaging/README.md)
for local builds, package-manager tests, supported platforms and release steps.
The standalone CLI currently emits unoptimized core Wasm; the development CLI
below also supports optimization and native WASI components.

### Python development CLI

```sh
pip install wasmtime        # runtime (alternatives: the wasmtime CLI >= 47, or Node 24+)
npm i -g binaryen           # optional optimizer (or brew/apt install binaryen)

python -m snekc run   examples/shapes.py
python -m snekc wat   examples/shapes.py     # print the generated WAT
python -m snekc build examples/shapes.py     # writes shapes.wasm
python -m snekc build examples/shapes.py --no-opt  # native encoder only; Python is enough
python -m snekc check examples/shapes.py
python -m snekc run examples/shapes.py --frontend snek  # opt-in Snek-written source parser
python tests/run_tests.py
python tests/check_formatting.py   # seeded format-spec stress tests
python tests/check_wasm.py         # native encoder and dependency-isolation regressions
```

Snek encodes core Wasm binaries itself, with no Python dependencies or external assembler. Builds optimize with `wasm-opt -O3` by default; use `--no-opt` with `build` or `run` to skip Binaryen entirely. Running a program still needs one of the runtimes above. The full differential suite exercises both raw and optimized builds, so it needs Binaryen too.

For native WASI 0.3 components, also install `wasm-tools` 1.250+ and the Wasmtime CLI 49+:

```sh
python -m snekc build examples/async_functions.py --component  # .wasm + standalone .wit
python -m snekc run examples/async_functions.py --component
python tests/check_components.py   # every example, raw and optimized, with WIT validation
python tests/check_component_lifecycle.py   # real host cancellation and repeated invocations
python tests/check_stdlib.py               # CLI streams, host errors, cancellation, both targets
python tests/check_host_library.py         # args/env, clocks/timers, random and ABI allocation
```

Components use the Wasmtime CLI; the Python Wasmtime runner and Node run core modules only. If you have forced another engine, set `SNEK_RUNTIME=wasmtime` for components. `SNEK_WASM_TOOLS` overrides the assembler command. Core preview1 output remains the default for compatibility.

- If `wasm-opt` isn't on your PATH, set `SNEK_WASM_OPT` to the command (for example `SNEK_WASM_OPT="npx wasm-opt"`).
- `SNEK_RUNTIME=wasmtime-py|wasmtime|node` picks the engine, and `SNEK_NODE` points at a specific Node binary.
- `--frontend cpython|snek` selects the source parser for `check`, `wat`, `build`, and `run`. `SNEK_FRONTEND` sets its default; an explicit flag wins. CPython remains the default. The Snek frontend currently runs with the Python shim and adapts its owned AST to the existing Python checker; it does not require a Wasm engine just to compile.
- `--compiler snek` runs the independent, self-hosted compiler. First run `python bootstrap/bootstrap_compiler.py`; then use, for example, `python -m snekc run examples/async_functions.py --compiler snek`. `SNEK_COMPILER=snek` selects it by default, and `SNEK_COMPILER_WASM` overrides its binary. It owns parsing, checking, lowering, runtime generation, emission and core binary encoding; missing or failed compiler binaries never fall back to Python. `--compiler python` retains the reference pipeline described below.

Tested with Python 3.13, Binaryen 132, wasm-tools 1.250, Wasmtime 49 (CLI and Python package), and Node 25. Node 24+ is supported; Node 22 is refused, for the reason given in DESIGN.md under "Engines".

## Pipeline

The reference compiler follows this pipeline. The self-hosted path performs the
same work inside `bootstrap/build/compiler.wasm`, using the Snek implementations
in `bootstrap/`; Python only supplies CLI/file/runtime orchestration. Binaryen
optimization and `wasm-tools` component linking remain optional external tools.

```
prog.py
  → frontend.py                        CPython parser (default), or Snek parser + AST adapter
  → check.py   declare pass            runtime/exceptions.snek, then classes, fields, vtable slots,
                                       signatures
               check/elaborate pass    inference, narrowing, definite assignment, argument binding
  → ir.py      typed IR                every type resolved, every conversion explicit; for-loops,
                                       f-strings, __init__ and super().__init__ already lowered
  → machines.py                        generator/coroutine CFGs, saved GC frames and resume functions
  → emit_wat.py + prelude.wat          WasmGC types, vtables, monomorphized lists, try_table
  → wasm.py                            native binary encoding of the compiler's folded WAT
  → wasm-opt (optional)                -O3 --closed-world; bypass with --no-opt
  → prog.wasm                          core module: imports wasi_snapshot_preview1
     or component.py + wasm-tools      --component: native wasi:cli@0.3.0 + standalone WIT
```

The IR is the language/backend seam. WAT remains the shared instruction representation for the emitter, hand-written runtime and native encoder; `wat` and `--keep-wat` retain readable output.

## Layout

| Path | What it is |
|---|---|
| `snekc/frontend.py` | Parser selection and temporary owned-AST adapter, including assignment contexts |
| `snekc/check.py` | Declaration pass and type checker; produces the IR |
| `snekc/ir.py` | The typed IR |
| `snekc/types.py` | Type representations, widening and assignability rules |
| `snekc/closures.py`, `closures_wat.py` | Lexical scopes, shared capture cells, typed callable objects and indirect calls |
| `snekc/runtime/*.snek`, `runtime_sources.py`, `builtins.py` | Authored exception/string/formatting runtime sources and their validated resource loader |
| `snekc/emit_wat.py` | IR to WAT: type section, vtables, per-element-type list runtime, `try_table` and `finally` lowering |
| `snekc/collections_wat.py` | Monomorphized tuple and dictionary runtime, insertion order, hashing and dictionary views |
| `snekc/list_builtins.py` | List reductions and stable merge sort |
| `snekc/ordered_builtins.py`, `keyed_builtins.py` | Typed key callbacks, ordered argument binding, cached-key sorting and live-list extrema |
| `snekc/protocols_wat.py` | Class equality and string dispatch, including subclass priority |
| `snekc/formatting.py` | Static format-spec validation; loads helpers from `runtime/formatting.snek` |
| `snekc/machines.py` | Stackless generator/coroutine frames, resume functions, exception and cleanup edges |
| `snekc/component.py`, `component_runtime.wat`, `wit/` | WASI 0.3 command world, canonical ABI wiring and callback-driven stream/future output |
| `snekc/stdlib.py`, `stdlib_io.py`, `component_input.wat`, `snek/sys.py`, `snek/io.py` | Standard-library namespaces, typed CLI stream views, native async stdin, and CPython equivalents |
| `snekc/stdlib_host.py`, `wasi_bindings.py`, `abi_memory.wat`, `snek/os.py`, `time.py`, `secrets.py` | Host configuration, clock/timer and secure-random bindings, temporary canonical data, and CPython equivalents |
| `snekc/prelude.wat` | Hand-written runtime: strings and `repr`, number formatting, Python division, checked conversions, exception reporting, WASI I/O |
| `snekc/wasm.py` | Dependency-free core binary encoder: symbols, recursive GC types, instructions, sections and debug names |
| `bootstrap/` | Independent Snek compiler, source-to-binary fixed-point proof, and differential/bootstrap checks |
| `snekc/driver.py`, `runner.py`, `__main__.py` | Build pipeline, Wasmtime/Node runner, CLI |
| `snek/__init__.py` | CPython shim (`i32 = int`, …) so programs run under CPython |
| `examples/` | Programs that double as differential tests |
| `tests/errors/` | Programs that must be rejected, each with its expected error |
| `DESIGN.md` | The language rules and the Wasm mapping, with reasons |
| `bench/` | Benchmarks against Pyodide, py2wasm, CPython WASI, RustPython, MicroPython and native CPython |

## What works

- **JavaScript libraries.** Export typed functions to Node and browsers with
  `snek build program.py --library --export function_name`. Builds include `.js`
  wrappers and `.d.ts` declarations, with scalar/string conversion and translated
  exceptions. Typed `@host("name")` declarations call JavaScript functions supplied
  by the application; async declarations await Promises, and async exports return
  Promises. See [host bindings](docs/reference/libraries.md).
- **Local modules and packages.** Ordinary imports, aliases, relative imports and
  re-exports of functions/classes from `.py` or `.snek` files. Package initialization
  runs once; cycles, ambiguous paths and root escapes are diagnosed. Both CLIs
  accept `--module-root`; the default is the entry file's directory. Try
  `python -m snekc run examples/modules/main.py --no-opt`. See
  [module rules](DESIGN.md#modules-and-imports) for the static subset and limits.
- **Machine numerics.** `i32`, `i64`, `f32`, `f64` and `bool`, with Python's `//` and `%` semantics.
  - Integer overflow wraps.
  - Float→int conversions raise like `int()`.
  - Ints mix into floats only where CPython's result is a float too.
- **Classes.** Reference types only, single inheritance, virtual methods and `Final` fields.
  - `super().__init__(...)` and `super().method(...)` work.
  - Closed-world compilation emits leaf classes as `final` and devirtualizes calls when only one implementation is reachable.
  - Objects are built with a single `struct.new`, using Swift-style two-phase initialization.
  - `__eq__`, `__str__`, and `__len__` work with inheritance and virtual dispatch. `bool(obj)` uses `__len__`; conditions still require explicit bools.
  - Class values (`type[C]`) support identity checks and `__name__`, including `type(obj)` for class instances.
- **Exceptions.**
  - `try`/`except`/`else`/`finally`, `raise`, bare `raise`, tuples of classes and `as e`.
  - The built-in hierarchy plus user exception classes, with `str(e)` and `type(e).__name__`.
  - Runtime errors such as `IndexError` and `ZeroDivisionError` are catchable, with CPython's messages.
  - An uncaught exception reports CPython's last traceback line and exits 1.
- **Context managers.** `with` calls `__enter__`/`__exit__`, supports multiple managers and `as` targets, and cleans up on exceptions, returns, breaks and continues. `__exit__` can suppress exceptions. Its type/value arguments are usable; tracebacks are opaque. See [the typed signatures](DESIGN.md#context-managers).
- **Functions.** Default values (literals) and keyword arguments, with CPython's evaluation order.
- **Optionals and narrowing.** `T | None` works for reference types. Locals narrow after `is None` and `is not None` checks, `isinstance`, `assert`, early exits, `except ... as`, and inside `and`/`or` and conditional expressions.
- **Strings.** UTF-8 `str` with CPython's code-point semantics: `len`, indexing, slicing, iteration and `in` count code points, with an ASCII fast path.
  - The common methods: `split`/`rsplit`/`splitlines`, `join`, `strip` and friends, `find`/`index`/`count`, `replace`, `startswith`/`endswith`, `upper`/`lower`/`title`/`casefold`, the `is…()` checks, `ljust`/`center`/`zfill` and more.
  - Unicode-correct, with tables embedded per program: `"straße".upper()` is `"STRASSE"`, and `i32("٤٢")` is 42.
  - f-strings (with `!r`/`!s` and literal format specs), `repr()`, `ord()`, `chr()`, `s * n`, and `str()` of numbers, bools, exceptions and lists.
  - Format width, alignment, Unicode fill, signs, grouping, integer bases, and float precision (`f`, `e`, `g`, `%`) with ties-to-even rounding. Dynamic specs and locale-aware `n` are not supported yet.
- **Lists.** `list[T]` is monomorphized per element type: literals, indexing, slicing, iteration, nesting, `in`, `+`, `*`, `==`, and `append`, `pop`, `insert`, `extend`, `remove`, `index`, `count`, `reverse`, `clear`, `copy`. `list[bool]` uses a packed i8 array.
  - `sum(xs, start=...)`, `min`/`max` (including `default=`), and stable `sorted(xs, reverse=True)`. Callable `key=` supports arbitrary typed elements using numeric, bool or string keys: `sorted(players, key=lambda p: p.score)`. Keys run once per visited element; ties retain input order even in reverse.
  - Float sums use compensated addition. A possibly empty float list needs a float start, such as `sum(xs, 0.0)`, so its result type stays fixed.
- **Dictionaries.** `dict[K, V]` uses open addressing and preserves insertion order: literals, indexing, assignment, `del`, `in`, equality, `get`, `setdefault`, `pop`, `popitem`, `update`, `clear`, and `copy`.
  - Keys are strings, bools, fixed-width integers, or tuples of supported keys; reference keys can be optional. Missing keys raise `KeyError` with the key's `repr`.
  - `keys()`, `values()`, and `items()` are live views: iterate them, test membership, take their length, or convert them with `list()`. `for key, value in d.items()` supports unpacking.
  - Numeric and bool values need an explicit `get()` default, since they cannot be `None`.
- **Tuples.** Fixed-length `tuple[T1, T2, ...]` types compile to immutable structs: literals, nesting, unpacking, multiple return values, literal indexing/slicing, concatenation, constant repetition, equality, and `repr`. Homogeneous tuples also support dynamic indexing, iteration, `in`, `count`, and `index`.
- **Generators.** `Iterator[T]` or `Generator[T, None, None]`, lazy `yield`, `for`, `iter`, `next` (including a same-type default), `list(generator)`, `send(None)`, `throw(exception)` and explicit `close()`. Locals, loops, handlers, `finally` and `with` survive suspension. Generator methods dispatch virtually too.
  - No `yield from`, sent values, generator return values or async generators yet. Close unfinished generators explicitly: WasmGC does not run Python's refcount-driven finalizers.
- **Async functions.** `async def`, `await`, typed results, async methods and nested cleanup. `from snek import run, checkpoint` provides a module-level entry point and a cooperative scheduling point, with CPython equivalents.
  - Core modules drive these frames locally; components return `YIELD`/`WAIT` to the WASI 0.3 callback scheduler. `await wait_all(...)` runs sibling coroutines cooperatively on core/library targets, unwinding siblings on failure and awaiting cleanup. It discards child results. Detached spawning and arbitrary awaitables remain future work; this is not an `asyncio` implementation. CLI streams and async timers are supported below.
- **Control flow.** `if`/`elif`/`else`, `while`, `for` over `range()`, strings, lists, dictionaries/views, homogeneous tuples and generators, `break`, `continue`, `assert`, and recursion.
- **Binary standard I/O (core target).** `from snek import read_bytes, write_bytes`: read stdin to EOF as `list[i32]`, or write values in `0..255` to stdout. This supports binary-producing tools such as the bootstrap encoder; `snekc run` preserves raw bytes. Components use the standard-library CLI streams below.
- **Standard-library CLI streams.** `from snek import sys, io` provides first-class, typed streams: UTF-8 output, byte-list binary I/O, bounded async reads, `readline()`, `flush()`, `await drain()`, `closed`, `close()`, and `with` cleanup. Components use native WASI 0.3 streams/futures; the core target keeps the same source API with blocking preview1 I/O. See the examples below.
- **Typed function values and closures.** `Callable[[i32], i32]` from `typing` or `collections.abc`, module functions as values, annotated nested functions, and context-typed lambdas. Escaping closures share captured variables, including `nonlocal` updates, recursion and loop late binding. They work with containers, ordinary methods, generators, async functions and components.
  - Callable signatures are invariant and calls require all arguments positionally. Bound-method values, closures inside `__init__`, lambda defaults and dynamic signatures are not supported yet. Shared captures are not smart-cast; copy an optional capture to a local before narrowing. See [DESIGN.md](DESIGN.md#closures-and-callable-values).

## Standard-library streams

```python
from snek import run, sys

async def main() -> None:
    sys.stderr.write("copying stdin\n")
    await sys.stderr.drain()
    data = await sys.stdin.buffer.read()
    sys.stdout.buffer.write(data)
    await sys.stdout.drain()

run(main())
```

Run this with `python -m snekc run examples/stdlib_pipe.py --component` (add
`--no-opt` to bypass Binaryen), or with CPython using the `snek` shim.

`stdout.write(text)` and `stderr.write(text)` return an `i32` character count;
`.buffer.write(data)` returns the number of `list[i32]` byte values accepted.
All byte values must be in `0..255`; invalid lists write no bytes. Text is
encoded as UTF-8. Writes preserve order with `print()`, and copy their data
before returning, so later list mutation cannot change queued output.

`flush()` submits buffered text without waiting for a component's host sink.
`await drain()` waits for queued output to finish and reports that stream's
errors as `OSError` or `BrokenPipeError`. On the sequential component scheduler,
draining either stream also services earlier writes to the other stream.
Programs producing substantial output should drain regularly to bound the
output queue. The runtime finishes pending output when the command exits.

`await stdin.buffer.read(size=-1)` returns a byte list. A negative size reads
through EOF; a nonnegative size reads up to that many bytes, filling the request
unless EOF arrives. `await stdin.buffer.readline(size=-1)` stops after a newline
(included in the result), the size limit, or EOF. Zero-sized reads return an
empty list without asking the host for input. Consecutive reads share one stdin
session, including buffered bytes after a newline. Input errors raise `OSError`
or `BrokenPipeError`. WASI stdin is imported only when a component reads input.
Text stdin and async chunk iteration remain future additions.

Streams are values: store them in locals or fields, return them, or pass them
to functions annotated with `io.BinaryInput`, `io.BinaryOutput`, `io.TextInput`,
or `io.TextOutput` (also importable directly from `snek.io`). For example:

```python
from snek import io, run, sys

async def copy(source: io.BinaryInput, sink: io.BinaryOutput) -> None:
    while True:
        chunk = await source.read(8192)
        if len(chunk) == 0:
            break
        sink.write(chunk)
        await sink.drain()

async def main() -> None:
    with sys.stdin.buffer as source, sys.stdout.buffer as sink:
        await copy(source, sink)

run(main())
```

See `examples/stdlib_incremental.py`. Bounded reads let this copy loop use
bounded memory and finish each chunk without waiting for EOF.

`close()` is idempotent; `with` calls it on normal, exceptional, and cancelled
exits. Closing a stream also closes its text/binary view and all aliases;
`closed` remains inspectable, while subsequent I/O raises `ValueError`. Output
close submits pending data but does not await it: drain before closing to
observe host errors. The runtime finishes queued output on command exit.
Closing stdin releases its retained canonical handles and lookahead. Command
completion/cancellation also releases them, and a repeated command invocation
starts with fresh stream views. These views do not close the host's underlying
standard-I/O descriptors. Constructing arbitrary streams is not supported yet.

Imports also support `from snek.sys import stdout as out`,
`from snek import sys as cli`, and `import snek.sys as cli`. Modules are static
namespaces; streams are first-class values, but bound methods are not.
Capability-backed filesystem operations, HTTP, and sockets remain future
library packages.

## Host configuration, clocks, and randomness

```python
from snek import os, run, secrets, sys, time

async def main() -> None:
    print(sys.argv[1:])
    print(os.getenv("MODE", "local"))
    start = time.monotonic()
    await time.sleep(0.01)
    print("elapsed:", time.monotonic() - start)
    print("request ID:", secrets.token_hex(8))

run(main())
```

`sys.argv` is a mutable `list[str]` cached for the command invocation. Its first
entry is the host's program name; the remaining entries are the supplied
arguments. `os.getenv(name)` returns `str | None`; a string default, as in
`os.getenv(name, "fallback")`, gives a `str` result. Environment reads are
limited to variables granted to the guest. For example:

```sh
python -m snekc run program.py --component --env MODE=demo -- first second
python -m snekc run program.py --component --env MODE  # inherit just MODE
```

No host environment variables are inherited by default. Library callers can
pass `runner.run_binary(binary, args, env={"MODE": "demo"})`; explicit grants
work with Wasmtime CLI, Python Wasmtime, and Node. The CPython shim reads its
ordinary process arguments/environment.

`time.time()` returns Unix seconds as `f64`; `time.monotonic()` returns elapsed
clock seconds from an unspecified origin. `await time.sleep(seconds)` waits
for at least the requested interval and accepts numeric arguments. Negative
or NaN intervals raise `ValueError`; infinite or unrepresentable intervals
raise `OverflowError`. Components use WASI 0.3 async `wait-for`, including
prompt cancellation and cleanup; the compatibility core target blocks in
preview1 `poll_oneoff`. The current scheduler handles one awaited operation
at a time and starts the timer after earlier queued I/O has drained.

`secrets.token_bytes(n)` returns exactly `n` byte values as `list[i32]` until a
dedicated `bytes` type is available. `secrets.token_hex(n)` returns `2*n`
lowercase hex characters, and `secrets.randbelow(n)` returns an unbiased `i32`
in `0..n-1`. Counts/bounds are `i32`, passed positionally and explicitly;
negative counts or nonpositive bounds raise `ValueError`. The runtime fills
short WASI 0.3 random reads, rejects zero/oversized results, and uses rejection
sampling for bounded integers. It uses only host-provided secure randomness;
there is no deterministic fallback.

The component imports only the environment, clock, and random interfaces its
compiled code uses. Variable-sized host results use a temporary linear-memory
arena above the CLI scratch page, then copy into private GC values. The arena
reuses its high-water allocation; it is not limited to a 64 KiB result. New
host I/O failures raise `OSError`, and malformed UTF-8 input is rejected.
These APIs also support module aliases and direct imports, such as
`from snek.time import sleep`. See `examples/stdlib_host.py` for a deterministic
example suitable for the differential suite.

## Performance

The suite now covers eleven workloads, including dictionaries, list builtins, protocols, generators and async calls. These are five-run medians in milliseconds on an Apple M5 Max (macOS, Node 25.9, Wasmtime 49, native CPython 3.13.14), from the [2026-10-11 snapshot](bench/results/2026-10-11.json). See [`bench/`](bench/README.md) for the full comparison, startup/allocation tests, component timings and caveats.

**String-fusion follow-up:** writing integers directly into the result buffer cuts the Wasmtime Python-hosted strings benchmark from 555.9 to 136.4 ms (**4.07×**) in a fresh paired run. Explicit DRC and copying CLI comparisons also improve. See the [separate results](bench/README.md#strings-fusion-follow-up); the full-suite tables below retain their original, pre-fusion timings.

| | fib | mandelbrot | binary_trees | sieve | strings | exceptions |
|---|---|---|---|---|---|---|
| snek (V8) | 2.0 | 2.9 | 9.2 | 5.2 | 21.9 | 0.9 |
| snek (Wasmtime) | 2.6 | 3.5 | 221.9 | 16.2 | 374.5 | 17.1 |
| CPython 3.13, native | 52.3 | 88.8 | 294.7 | 48.7 | 80.8 | 96.3 |
| Pyodide (V8) | 78.7 | 174.9 | 477.5 | 70.8 | 194.5 | 212.1 |
| CPython 3.12 WASI build (Wasmtime) | 232.4 | 458.4 | 2,175.3 | 226.5 | 525.1 | 733.1 |

| | dicts | list_builtins | protocols | generators | async_calls |
|---|---|---|---|---|---|
| snek (V8) | 3.0 | 9.8 | 0.9 | 4.0 | 1.9 |
| snek (Wasmtime) | 18.9 | 18.6 | 3.1 | 14.6 | 10.0 |
| CPython 3.13, native | 13.9 | 40.1 | 48.0 | 17.5 | 22.3 |

- Core benchmark modules are 0.6–3.6 KB; the empty module is 291 bytes.
- Wasmtime remains much slower than V8 on allocation-heavy workloads; strings and tuple-key dictionaries are also slower than native CPython in this run.
- Async measures one coroutine's nested awaits and checkpoints, not concurrent I/O. Component results include cold CLI startup and compilation and are reported separately.
- The older Intel cloud snapshot remains available, but it is not a same-machine regression baseline.

## Roadmap, roughly in order

1. **Done: `dict[K, V]` and tuples.** Ordered dictionaries with open-addressed lookup and cached string hashes; fixed-length tuples as immutable structs. See [DESIGN.md](DESIGN.md#dictionaries) for supported operations and current limits.
2. **Done: `sorted`, `sum`, `min`/`max` over lists, and literal format specs in f-strings.** See [DESIGN.md](DESIGN.md#list-builtins) for typing rules and current limits.
3. **Done: special methods and context managers.** `__eq__`, `__str__`, `__len__`, and `with` via `__enter__`/`__exit__`. See [DESIGN.md](DESIGN.md#special-methods) for the typed subset and remaining protocols.
4. **Done: typed generators and `async def`.** Both lower to GC-backed state machines; component execution uses WASI 0.3's callback ABI. See [DESIGN.md](DESIGN.md#generators-and-coroutines) for supported operations and limits.
5. **Done: component model output with WIT.** `--component` emits a native WASI 0.3 command, with no preview1 adapter/imports. **Still pending:** adopting the GC canonical ABI when sufficiently supported; current boundaries use linear memory, while language values stay on the GC heap.
6. **Done: typed closures and lambdas.** A typed funcref paired with a GC environment of shared cells, with `nonlocal` and escaping captures. Callable `key=` for sorting/extrema is implemented too. See [DESIGN.md](DESIGN.md#closures-and-callable-values) for the supported subset.
7. **Done: own core binary encoder.** `--no-opt` builds need only Python; `wasm-opt` is used only for optimization. Component packaging still uses `wasm-tools`. See [DESIGN.md](DESIGN.md#native-binary-encoding).
8. **Done: self-hosting.** The independent compiler consumes its unchanged Snek source and emits byte-identical stage-2 and stage-3 compiler binaries. Parsing, checking, closure/suspension lowering, runtime and Unicode generation, ordinary emission, module assembly and core binary encoding run in Snek, with Python compiler entry points disabled during the proof. `--compiler snek` supports core and component builds; Python remains the default reference implementation. Component packaging still uses the host linker and `wasm-tools`. See [`bootstrap/`](bootstrap/README.md) for reproducible proof and regression commands.
9. **Done: local modules and packages.** Static local imports, aliases, relative imports, function/class re-exports and initialization ordering work through the shared Snek-owned linker, in both compilers and the native CLI. `--module-root` selects the source root. See [module rules](DESIGN.md#modules-and-imports); dynamic imports, module globals and package registry resolution remain out of scope.
10. **Done: JavaScript library exports and host imports.** `build --library --export FUNCTION` emits Wasm, ES-module JavaScript bindings and TypeScript declarations. Both compilers and the native CLI support typed synchronous and async boundaries, Promise-returning async exports, once-only initialization, and exception translation. See the [host bindings guide](docs/reference/libraries.md). Additional collections and user-defined WIT interfaces remain future work.
11. **Done: first-class browser support.** Capability checks and actionable loading errors, browser-engine CI, worker examples, sync/async host imports, tested Vite/TypeScript bundling, and an interactive docs playground are implemented. The playground runs the self-hosted compiler and compiled programs locally in a worker. JavaScript owns DOM objects and events; Snek calls application-supplied functions and awaits Promises. See the [browser support guide](docs/reference/browsers.md).
12. **Done: opaque JavaScript objects.** `HostObject` and `HostObject | None` cross library boundaries as GC-managed external references. Snek can store them in fields, collections, closures and suspended coroutines and pass them to typed host functions. Identity is preserved without manual handle tables. Properties and methods remain host-owned. See [opaque JavaScript objects](docs/reference/libraries.md#opaque-javascript-objects).
13. **Done: typed JavaScript callbacks.** Synchronous `Callable` values cross library boundaries in both directions, with typed JavaScript wrappers, GC-managed captures, identity-preserving round trips and deferred event handlers. Both compilers and the native CLI generate the same bindings and TypeScript declarations. See [typed callbacks](docs/reference/libraries.md#typed-callbacks).
14. **Done: concurrent JavaScript-driven async calls.** Multiple async invocations can overlap on one instance, including calls to the same export, with per-invocation GC frames and host-result state. Sync exports and callbacks can run between suspended steps; synchronous re-entry remains rejected. See [concurrent calls](docs/reference/libraries.md#concurrent-calls).
15. **Done: typed async callbacks.** `Callable` signatures returning `Coroutine[None, None, T]` accept JavaScript Promise-returning functions and expose Snek async closures as awaitable JavaScript functions. Rejections become catchable Snek errors, cleanup can await, and concurrent calls retain independent frames. See [async callbacks](docs/reference/libraries.md#async-callbacks).
16. **Done: structured sibling coroutines.** `await wait_all(...)` starts typed child coroutines, discards their results, and unwinds siblings on failure while waiting for awaited cleanup. Core/library targets support nested groups; JavaScript libraries can overlap host I/O through GC-managed request records. See [structured concurrency](docs/reference/libraries.md#structured-concurrency-with-wait_all).
17. **Done: typed result collection.** `await gather(...)` collects matching non-`None` child results into `list[T]` in argument order, including references, nested collections and callbacks. It shares `wait_all`'s structured failure/cleanup behavior and has a CPython shim. See [gather](docs/reference/libraries.md#collecting-results-with-gather).
18. **Done: cooperative library cancellation.** Async exports accept optional `{signal}` call options. Aborting unwinds the invocation and nested groups, waits for awaited cleanup, and rejects with `SnekCancelledError`; unrelated invocations continue. Host operations remain application-owned. See [cancellation](docs/reference/libraries.md#cancelling-an-async-export). Detached spawning, Snek-side public cancellation and component task groups remain future work.
19. **Done: typed list handles.** Snek-owned lists of scalars, strings and object handles cross synchronous and async library boundaries as GC-managed `SnekList<T>` handles. JavaScript can inspect and mutate the live list, including inside host callbacks, while identity and instance ownership are preserved. See [list handles](docs/reference/libraries.md#snek-owned-list-handles). Additional collections remain future work.
20. **Done: nested list boundaries.** Lists of supported lists cross as nested typed handles, preserving shared children and GC ownership. Nested lists work in synchronous/async host calls and exports, including `gather()` results. `toArray()` stays shallow so copying is explicit. See [nested lists](docs/reference/libraries.md#nested-lists).
21. **Done: list-aware callbacks.** Sync and async `Callable` boundaries accept and return supported list handles, including nested lists. List types used only in callback signatures still generate wrappers and TypeScript declarations, with the same identity and instance-ownership checks. See [callbacks with lists](docs/reference/libraries.md#callbacks-with-list-handles).
22. **Done: opaque Snek class handles.** Class instances and nullable class references cross direct sync/async library boundaries as GC-rooted `SnekObject` handles. Identity survives base/derived annotations, runtime checks reject unrelated or foreign-instance values, and generated TypeScript preserves nominal typing and upcasts. Fields and methods remain behind explicit exports. See [class handles](docs/reference/libraries.md#snek-owned-class-handles).
23. **Done: class handles in lists and callbacks.** Class and nullable class references compose with nested lists and sync/async callback signatures. Every path shares identity and ownership checks; individual objects support upcasts while mutable lists stay invariant. See [composed class boundaries](docs/reference/libraries.md#classes-in-lists-and-callbacks).
24. **Done: lists of callbacks.** Sync and async functions cross list boundaries with typed parameters/results, stable identity, and retained captures. Hosts can register functions for later use without re-entering an active Snek step. Nested lists and callback-list parameters share the same bridge. See [lists of callbacks](docs/reference/libraries.md#lists-of-callbacks).
25. **Done: higher-order callbacks.** Callback parameters and results may themselves be typed callbacks, including async factories and decorators. Nested signatures share function identity, captured state, validation, and generated TypeScript, while synchronous re-entry remains rejected. See [higher-order callbacks](docs/reference/libraries.md#higher-order-callbacks).
26. **Done: typed dictionary handles.** Dictionaries with string, bool, i32 or i64 keys cross library boundaries as live `SnekDict<K, V>` handles. Typed mutation, missing-key handling, insertion-ordered snapshots, nested values, callbacks, and sync/async hosts preserve GC ownership and identity. See [dictionary handles](docs/reference/libraries.md#snek-owned-dictionary-handles).
27. **Done: typed tuple handles.** Fixed-shape tuples cross as immutable `SnekTuple` handles with positional TypeScript types, checked field access, and frozen shallow array copies. Empty tuples, nested collections, class and callback fields, and sync/async boundaries retain identity and GC ownership. See [tuple handles](docs/reference/libraries.md#snek-owned-tuple-handles).
28. **Done: tuple dictionary keys.** Immutable tuple keys, including empty and nested tuples, cross dictionary boundaries with Snek's structural hashing and equality. Equal handles find the same entry while updates retain the original key and insertion position. See [tuple keys](docs/reference/libraries.md#tuple-dictionary-keys).
29. **Done: nullable reference boundaries.** Strings, collections, tuples and callbacks support `T | None` alongside class and host objects. `None` maps to `null`, non-null handles preserve identity, and nullable string/tuple keys retain dictionary semantics. Sync/async hosts and nested callback signatures share the same validation and TypeScript narrowing. See [nullable references](docs/reference/libraries.md#nullable-references).
30. **Done: JavaScript iterator interop.** Snek generators cross as typed, GC-rooted `SnekIterator` handles. JavaScript iteration advances lazily and closes generators on early loop exit, with checked ownership, nested yield types, nullable handles and exception translation. See [iterator handles](docs/reference/libraries.md#snek-owned-iterator-handles).
31. **Done: library interface validation.** Generated JavaScript verifies the complete Wasm boundary schema before module initialization, rejecting stale bindings with incompatible scalar, host, callback or nested handle types. Both compiler paths and optimized builds share the check. See [loading](docs/reference/browsers.md#compatibility-and-loading).
32. **Done: ordered dictionary union.** Matching dictionaries support shallow-copy `|` and in-place `|=`, preserving key order, aliases and Python's operand evaluation order. Both compiler paths reuse the existing dictionary helpers. See [dictionary semantics](DESIGN.md#dictionaries) and [the example](examples/dict_union.py).
33. **Done: dictionary literal unpacking.** `{**base, "key": value, **overrides}` merges matching typed dictionaries in source order, with shallow copies and stable key positions. Earlier unpacks are copied before later expressions execute or suspend. See [dictionary semantics](DESIGN.md#dictionaries) and [the example](examples/dict_unpack.py).
