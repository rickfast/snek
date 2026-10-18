# snek benchmarks

How snek compares with other ways of running Python as WebAssembly, with native CPython as the reference. Every benchmark is a single snek source file that also runs as Python. Interpreter inputs have type annotations erased and a small compatibility shim added; workload operations are unchanged. Every repetition's output is checked against native CPython before its time counts.

## Runtimes

The harnesses share statistics and source hashing in `bench/common.py`.
Source identity includes both compiler implementations, runtime resources,
native/package sources, and workloads; it excludes result snapshots. Timing
boundaries remain separate: warm-engine, fresh-process, and internal phase
measurements must not be compared as if they measured the same operation.

| Runtime | What it is | Engine |
|---|---|---|
| snek (V8) | This compiler's output: WasmGC + Wasm exception handling | Node 25.9.0 |
| snek (Wasmtime) | The same `.wasm` file | wasmtime-py 49.0.0 |
| py2wasm | Wasmer's ahead-of-time compiler: Nuitka C plus a bundled CPython 3.11, built for WASI | Wasmtime 49 |
| CPython, native | CPython 3.13.14 on macOS, the reference | native |
| Pyodide | CPython 3.14 built with Emscripten (`pyodide` 314.0.7) | Node 25.9.0 |
| CPython WASI | VMware Labs' CPython 3.12 WASI build (`@antonz/python-wasi`) | Wasmtime 49 |
| RustPython WASI | RustPython built for WASI (`rustpython-wasi`) | Wasmtime 49 |
| MicroPython | MicroPython 1.29, PyScript build with Emscripten | Node 25.9.0 |

The optional `component` command measures native WASI 0.3 components using the Wasmtime 49.0.2 CLI. Its cold-process timings are reported separately from compute times.

## Benchmarks

| | Exercises |
|---|---|
| `fib` | Recursive calls and integer arithmetic (`fib(30)`) |
| `mandelbrot` | Tight floating-point loops (200×200, 100 iterations) |
| `binary_trees` | Allocation and garbage collection (the benchmarks-game program, depth 14) |
| `sieve` | List appends and indexing (`list[bool]`, 5 × 200,000) |
| `strings` | f-strings, concatenation and comparison (600,000 strings) |
| `exceptions` | Raising and catching a user exception class (1,000,000 iterations, one in three raises) |
| `dicts` | Tuple-key counting, growth, lookups and iteration (200,000 updates, 8,192 keys) |
| `list_builtins` | `sorted`, `sum`, `min`, `max` (50 lists of 10,000 unsorted integers) |
| `protocols` | `with`, `__enter__`, `__exit__`, `__len__`, `__eq__` (300,000 iterations) |
| `generators` | Three-stage lazy pipeline: generate, transform and filter 300,000 values |
| `async_calls` | 100,000 nested coroutine calls with 1,000 cooperative checkpoints |

## Compute and JavaScript boundary refresh — 2026-10-18

[Compute samples](results/2026-10-18-compute.json) refresh all eleven workloads on
an 18-core Apple M5 Max, Node 25.9.0, Wasmtime Python 49.0.0, CPython 3.13.14,
and Binaryen 132. The self-hosted compiler builds the optimized modules. Ten
repetitions per workload/runtime, with every output checked against CPython.
These retain the existing timing boundaries: Snek instantiation plus `_start`
with a compiled module, versus the interpreter's timed `main()`. No discarded
warmup; all samples, including first-run tiering costs, are retained. No local
build/test jobs ran alongside the measurements. This is a new baseline, not a
controlled before/after speedup claim.

Median milliseconds:

| Workload | Snek / V8 | Snek / Wasmtime | Native CPython |
| --- | ---: | ---: | ---: |
| Strings | 14.92 | 102.53 | 82.81 |
| Dictionaries | 3.43 | 20.71 | 12.85 |
| List built-ins | 11.06 | 20.32 | 40.91 |
| Generator pipeline | 4.71 | 15.70 | 17.79 |

Wasmtime remains slower than CPython on these string and dictionary workloads.
The generator pipeline is modestly faster on Wasmtime, and substantially faster
on V8. Optimized command modules for these four workloads are 2,025–2,840 bytes
(1,185–1,555 gzip bytes); per-module sizes and hashes accompany the samples.

The new [boundary benchmark](results/2026-10-18-library.json) measures a warm
library instance in Node, not browser startup or whole-program compute. It uses
three discarded warmup batches, ten measured batches of 10,000 operations,
rotating case order, and a checked checksum for every batch. Compilation,
instantiation, fixture setup, and generator creation are outside the timer;
the JS loop, checksum accumulation, bindings, handle/root management, and Wasm
call are inside. Async measurements include awaiting an immediately resolved
host Promise, not network or timer latency. The JS-only control is reported
separately and is not subtracted. Load average was about 6.9 on 18 cores.

Optimized library median ± MAD, nanoseconds per operation:

| Operation | ns/op |
| --- | ---: |
| Scalar export | 121 ± 2 |
| Five-byte string roundtrip | 379 ± 8 |
| 1,152-byte Unicode string roundtrip | 3,020 ± 25 |
| List handle roundtrip | 169 ± 5 |
| Dictionary handle roundtrip | 164 ± 2 |
| List element read | 87 ± 1 |
| Dictionary lookup, including string key copy | 384 ± 5 |
| Typed callback roundtrip | 250 ± 3 |
| Generator `next()` | 162 ± 6 |
| Async host roundtrip | 753 ± 15 |

The combined benchmark library is 31,118 bytes raw or 20,310 optimized
(7,579 gzip bytes optimized). Generated JS adds 30,200 bytes (7,472 gzip), with
2,268 bytes of TypeScript declarations. These are this library's sizes, not a
minimum bundle-size claim. Collection handles avoid copying collection contents;
strings still cross through UTF-8 encoding/decoding. Small raw/optimized timing
differences are not evidence that one mode is faster.

Reproduce with the pinned tools and a fresh verified compiler bootstrap:

```sh
SNEK_COMPILER=snek python bench/bench.py run --reps 10 \
  --runtimes snek-v8,snek-wasmtime,cpython-native --out bench/work/compute.json
python bench/library_bench.py --reps 10 --iterations 10000 --out bench/work/library.json
```

Snapshots record the exact source hash, including JavaScript runtime sources,
and dirty-tree status because the benchmark harness was added in this run.

## Lazy runtime-helper parsing — 2026-10-15

The compiler now registers 63 lightweight runtime-helper source entries and
parses each helper only when first needed, including transitive dependencies.
ASTs and signatures are cached per compilation. The fixed library is fully
validated/indexed by the owned parser during bootstrap preparation; only source
fragments, names and original line numbers are embedded. No CPython ASTs or
prechecked bodies enter the self-hosted compiler.

The [phase profile](results/2026-10-15-compiler-profile-lazy-runtime.json) measures
tiny-input `runtime_index` at **0.19 ms**, versus about **73 ms** for eager
`parse_runtime` in the preceding profile. Helpers actually used by a program are
now parsed inside `check_bodies`; this is avoided unused work, not elimination of
all runtime parsing. Tiny input uses no helpers. Collector configuration remains
DRC; native runner and encoder behavior are unchanged.

[Full paired results](results/2026-10-15-native-lazy-runtime.json) and a
[follow-up run comparison](results/2026-10-15-native-lazy-runtime-repeat.json)
retain every sample, including noisy ones. Ten repetitions per route (three for
self-compilation), one discarded warmup, rotating/interleaved order. Internal
`compiler_execute_ms` medians ± MAD from the full comparison:

| Input | Before: eager helpers | After: lazy helpers |
| --- | ---: | ---: |
| Tiny | 182 ± 4 ms | 102 ± 7 ms |
| 100 functions | 281 ± 8 ms | 183 ± 9 ms |
| 1,000 functions | 1,128 ± 14 ms | 1,020 ± 8 ms |
| Updated compiler bundle | 19,958 ± 181 ms | 19,975 ± 183 ms |

Tiny-input compiler execution takes about **44% less time** in this paired run;
self-compilation is unchanged within noise. These are internal wall-clock
intervals, not CPU time and not full command latency. The repeat also shows lower
compiler execution medians: tiny `run` 190 → 103 ms, strings `run` 202 → 115 ms.

**Full-process wall times are inconclusive under heavy machine load.** The
18-core laptop's one-minute load was 33 → 48 over the full run and 38 → 34 over
the repeat. Tiny-build wall time was 761 ± 241 → 760 ± 340 ms. In the repeat,
tiny `run` was 342 ± 75 → 471 ± 121 ms while strings `run` was
844 ± 176 → 679 ± 284 ms. The inconsistent directions and large dispersion mean
we do not claim an end-to-end speedup from these snapshots. Rerun on an idle
machine before using wall times in performance claims. Build/test jobs had ended
before measurements; unrelated machine load was not controlled.

Executable size grows by 99,072 bytes (about 97 KiB), from 18,924,960 to
19,024,032 bytes. Peak RSS is approximately unchanged: about 31 MiB tiny and
58 MiB large; self-compilation is 365 → 367 MiB. Baseline SHA-256:
`97236aa9110dcf703fdd63d1b864eb9e314cc70c2f8d7a493121abbb60cd08c8`.
New binary SHA-256:
`6bda3f8dccbe0a4b82bb0f7b2660d9554f4bbcee4d94300407bfe9ca08bcc5cf`.

Direct baseline/new comparisons confirmed identical binaries for all 60 examples
and identical diagnostics for all 163 negative fixtures. The fresh bootstrap
retains stage 2 = stage 3 and the no-host-fallback guards. Validation also passed:
9 focused lazy-runtime tests (all 63 helper ASTs/locations, lazy/cache behavior,
eager/lazy typed-IR parity), parser tests including optimized self-parsing,
8 self-host integration tests, 223 raw/optimized language cases, native CLI and
corpus tests, 27 component/host-I/O tests with no skips, the no-JIT Rust test,
and rebuilt local npx/bunx/uvx package tests. Hosted cross-platform CI is pending.

## Encoder lookup-table reuse — 2026-10-15

The [DRC phase profile](results/2026-10-15-compiler-profile-drc.json) identified
binary encoding as the largest tiny-input phase: about 130 ms, versus 72 ms
parsing runtime helpers. Inspection found fixed opcode/type dictionaries being
constructed for each visited instruction or value type. They now live on the
encoder instance and are reused for the whole module. No parser, generated
program behavior, garbage collector, or native runner configuration changed.

The [follow-up phase profile](results/2026-10-15-compiler-profile-tables.json)
measured encoding at about 110 ms for tiny input and 400 ms for 1,000 functions
(previously 503 ms). These separate diagnostic runs are not controlled A/B
measurements. The [initial exploratory profile](results/2026-10-15-compiler-profile-auto.json)
did not pin the CLI collector and is excluded from optimization comparisons.

The [paired native benchmark](results/2026-10-15-native-encoder-tables.json) uses
the saved AOT binary as its baseline, ten repetitions per route (three for
self-compilation), one discarded warmup, and rotating/interleaved order.
Median ± MAD wall time, milliseconds:

| Operation | Before: per-instruction tables | After: per-module tables |
| --- | ---: | ---: |
| Build tiny source | 329 ± 22 | 281 ± 28 |
| Build 100 functions | 409 ± 27 | 363 ± 21 |
| Build 1,000 functions | 1,168 ± 14 | 1,036 ± 5 |
| Rebuild compiler bundle | 17,889 ± 18 | 16,531 ± 15 |
| Compile and run tiny source | 245 ± 6 | 213 ± 8 |
| Compile and run strings workload | 360 ± 4 | 335 ± 4 |

That is roughly 15% less tiny-build time, 11% less large-build time and 8% less
self-compilation time in this paired run. Host load fell from about 22 to 7
during the run on the 18-core M5 Max; use the within-run comparisons, not the
absolute times from earlier snapshots. No build/test jobs ran alongside it.
The strings row includes compilation and is not a generated-code speedup.

Peak RSS is essentially unchanged (about 31 MiB tiny, 58 MiB large, 363 MiB self),
as is executable size at 18,924,960 bytes. Baseline SHA-256:
`23bf218e30bf26d1ca3aa8863421af63e787e035a9bf590ed689d9a0fe428d97`.
New binary SHA-256:
`97236aa9110dcf703fdd63d1b864eb9e314cc70c2f8d7a493121abbb60cd08c8`.

Both binaries produce identical bytes for every compared input, including the
updated compiler source. The fresh bootstrap preserves stage 2 = stage 3, with
no host compiler passes after the seed. Validation passed: 11 encoder tests,
8 self-host integration tests, 223 raw/optimized language cases, 11 native tests
plus its 223-case corpus, 27 component/host-I/O tests with no skips, the no-JIT
Rust test, and rebuilt local npx/bunx/uvx package tests. Profiler and benchmark
regression tests also pass. Hosted cross-platform CI has not run locally.

The remaining fixed runtime-helper parse cost is a candidate for the next
optimization; this change leaves it untouched.

## Precompiled embedded compiler — 2026-10-15

[Paired results and raw samples](results/2026-10-15-native-aot.json) compare the
saved October 14 executable with the new build-time-precompiled compiler on the
same Apple M5 Max. Ten repetitions per route, three for self-compilation, one
discarded warmup, rotating/interleaved order, fresh processes. These are still
working-machine measurements: the one-minute load average was about 15–17 on
18 cores. No build or test suite was running alongside this benchmark.

Wall-time median ± MAD, milliseconds; all generated Wasm is unoptimized:

| Operation | Before: compiler JIT | After: embedded AOT |
| --- | ---: | ---: |
| Build tiny source | 406 ± 15 | 262 ± 12 |
| Build 100 functions | 492 ± 8 | 360 ± 6 |
| Build 1,000 functions | 1,456 ± 12 | 1,291 ± 29 |
| Rebuild compiler bundle | 20,705 ± 88 | 20,351 ± 524 |
| Compile and run tiny source | 457 ± 12 | 288 ± 6 |
| Compile and run strings workload | 607 ± 21 | 423 ± 15 |

Tiny-build compiler loading drops from **148.94 ms to 2.73 ms**. Execution of
the compiler itself is essentially unchanged there (229 vs 231 ms). Tiny-build
wall time is about 35% lower; self-compilation has no clear speedup beyond noise.
The strings row includes source compilation, guest JIT and execution; it is not
a claim of faster generated string operations.

Median peak RSS drops from **234 to 31 MiB** for tiny builds, **259 to 58 MiB**
for 1,000 functions, and **537 to 364 MiB** for self-compilation. Avoiding compiler
JIT removes its transient compilation allocations from each process.

The tradeoff is executable size: **12.36 → 18.05 MiB** (12,963,776 → 18,924,960
bytes). The original binary SHA-256 is
`c211d9ca0c09882ceb7b25c8923155b4c33ed9b127d7418ba6471421331d850e`;
the AOT binary is
`23bf218e30bf26d1ca3aa8863421af63e787e035a9bf590ed689d9a0fe428d97`.

The build verifies the unchanged bootstrap proof, then precompiles stage 3 for
Cargo's target without inferring build-host CPU features. Only this trusted,
embedded image is deserialized. Guest programs retain normal Wasmtime validation
and host-specific JIT compilation. All compared compiler outputs are
byte-identical, and every self-build reproduces stage 3 exactly. The no-JIT Rust
test, 11 native CLI tests, 223 example/diagnostic cases, and rebuilt local
npx/bunx/uvx package tests passed. Other platforms remain gated on hosted CI.

The next compiler-speed target is the remaining `_start` execution time:
built-in/runtime setup and the compiler passes. This change does not profile or
optimize those passes.

## Standalone compiler baseline — 2026-10-14

[Full results and raw samples](results/2026-10-14-native.json) and
[separate launcher repeat](results/2026-10-14-launchers-repeat.json), measured on
the 18-core Apple M5 Max, macOS 26.6.2. Both runs use the same 12.36 MiB native
executable (SHA-256 `c211d9ca0c09882ceb7b25c8923155b4c33ed9b127d7418ba6471421331d850e`).
The embedded compiler and native runtime are unchanged between the two runs.
The repeat adds package-tool versions and load averages to the harness metadata.

These are working-machine measurements, not a controlled idle-machine result.
The repeat recorded a one-minute load average of about 25 on 18 cores. Package
launch latency varied substantially between runs, so retain the raw samples and
do not read small differences as regressions. Every measured output passed its
correctness check. The initial failed run, caused by a generated fixture using
the reserved name `f32`, is excluded; the complete run starts from scratch.

### Compilation and memory

Fresh processes, one discarded warmup per route, five measured repetitions
(three for self-compilation). Routes are interleaved and their order rotates.
Numbers are median ± median absolute deviation, in milliseconds. All builds
are **unoptimized**. The Python self-hosted route is the existing
`python -m snekc build --compiler snek --no-opt` CLI, including its WAT round
trip; the native route uses the compiler's direct source-to-binary entry point.

| Input | Native `snek` | Python self-hosted launcher | Python reference compiler |
| --- | ---: | ---: | ---: |
| Tiny, 10 bytes | 391 ± 2 | 707 ± 7 | 149 ± 4 |
| 100 functions, 8.1 KiB | 469 ± 15 | 685 ± 20 | 134 ± 8 |
| 1,000 functions, 82.8 KiB | 1,471 ± 15 | 1,624 ± 12 | 246 ± 11 |
| Compiler bundle, 1.68 MiB | 19,421 ± 346 | 19,363 ± 464 | not measured |

The bundle has 44,855 lines, including generated Unicode tables. Every native
and Python-selfhost output is byte-identical for the same input; self-compilation
reproduces stage 3 exactly. The reference compiler's generated programs are
checked for equivalent output, not identical binary layout.

Median peak RSS in MiB, measured per child with `wait4`:

| Input | Native | Python self-hosted | Python reference |
| --- | ---: | ---: | ---: |
| Tiny | 235 | 288 | 33 |
| 100 functions | 229 | 281 | 33 |
| 1,000 functions | 258 | 310 | 47 |
| Compiler bundle | 537 | 876 | not measured |

The standalone launcher reduces tiny-build wall time by about 1.81× versus the
Python self-hosted CLI. Self-compilation time is effectively tied, with about
39% less peak RSS in the standalone process. The Python reference compiler is
still much faster for the measured ordinary-source inputs; self-hosting has not
yet delivered a compilation-speed win over it.

### Where the native time goes

For the tiny build, the internal medians are **143 ms loading/JIT-compiling the
embedded compiler**, **216 ms executing its `_start`**, and less than 1 ms each
for engine creation and compiler instantiation. Internal total is about 365 ms;
the externally measured process wall time is 391 ms. These are separate medians,
so their sums need not equal the median total exactly.

Compiler execution includes built-in/runtime setup, parsing, checking, emission
and encoding; it is not a parser-only timing. For the 1,000-function input it
takes 1,293 ms, and for self-compilation 19.20 s. Precompiling the embedded Wasm
could address the roughly 143–152 ms JIT cost, but would not remove that compiler
execution time. Profiling built-in/runtime setup and the owned compiler passes
is the next separate optimization target.

### End-to-end `run`

Fresh process, source compilation and program execution combined; unoptimized
Wasm. Five-run medians in milliseconds:

| Workload | Native | Python self-hosted | Python reference | CPython |
| --- | ---: | ---: | ---: | ---: |
| Tiny | 492 | 1,016 | 415 | 177 |
| Strings | 777 | 1,134 | 444 | 302 |

Fast generated code does not by itself make a compile-and-run invocation faster
than CPython for short programs. The compute-only table below excludes compiler
and engine startup and answers a different question.

### Package-manager launches

These are **offline local-package** measurements, not published registry installs.
The 10-repetition repeat used npm 11.12.1, Bun 1.4.0 and uv 0.11.31, with one
discarded warmup. `bunx` uses `--bun --no-install`; `npx` uses `--offline`; `uvx`
uses the already-cached local wheel and the same Python interpreter.

| Cached route | `--version`, median ± MAD ms | Compile and run tiny, median ± MAD ms |
| --- | ---: | ---: |
| Direct native binary | 22 ± 9 | 478 ± 29 |
| npx | 473 ± 17 | 962 ± 41 |
| bunx | 145 ± 46 | 598 ± 60 |
| uvx | 227 ± 59 | 712 ± 64 |

The earlier full run's version medians were 42/1,177/705/1,670 ms respectively;
the large change is why both datasets are retained. These numbers are a baseline
for this machine and workload, not a general ranking of package managers.

First local use is kept separate: npm installation alone took 751 ms in the
full run and 672 ms in the repeat; uvx installation **plus first `--version`**
took 3,085 ms and 1,097 ms. Each is one observation, not a repeated median, and
they do not time identical operations. Bun reuses the npm-installed local tree,
so Bun install time is not measured. Public registry resolution, downloads and
network latency remain unmeasured until publication.

### Generated-code execution: raw versus optimized

All eleven existing workloads, five measured runs after one discarded warmup.
V8/Wasmtime reuse a compiled module and time instantiation + `_start`; CPython
times `main()`. Wasmtime here is the **49.0.0 Python binding with its default
collector**, distinct from the native launcher's Wasmtime 49.0.2/DRC. Optimized
modules use Binaryen 132 `-O3 --closed-world`. Times are median milliseconds;
variability, peak worker RSS, module sizes and hashes are in the full JSON.

| Workload | CPython | V8 raw | V8 O3 | Wasmtime raw | Wasmtime O3 |
| --- | ---: | ---: | ---: | ---: | ---: |
| fib | 57.99 | 2.30 | 2.40 | 4.39 | 2.92 |
| mandelbrot | 93.49 | 3.13 | 3.44 | 3.86 | 3.84 |
| binary_trees | 345.77 | 12.65 | 11.59 | 250.10 | 247.31 |
| sieve | 50.70 | 6.29 | 5.86 | 20.10 | 17.84 |
| strings | 87.11 | 18.84 | 13.52 | 112.05 | 115.89 |
| exceptions | 105.18 | 20.14 | 1.15 | 79.36 | 18.03 |
| dicts | 14.57 | 3.00 | 4.15 | 25.73 | 22.59 |
| list_builtins | 46.45 | 11.51 | 13.24 | 22.63 | 22.70 |
| protocols | 54.74 | 1.21 | 1.76 | 5.40 | 3.79 |
| generators | 19.08 | 4.74 | 4.68 | 14.96 | 16.54 |
| async_calls | 24.47 | 2.97 | 3.05 | 14.01 | 11.63 |

Optimization is especially valuable for exceptions in this run, but is not a
uniform win on every workload. Wasmtime strings and dictionaries remain slower
than native CPython here. Do not infer before/after speedups from the older
snapshots: this run uses the self-hosted compiler, discards a warmup, and has
different host activity. The other Python-in-Wasm interpreters and component
backend were not retimed in this standalone follow-up.

## Historical results — 2026-10-11

From [`results/2026-10-11.json`](results/2026-10-11.json), on an 18-core Apple M5 Max, macOS 26.6.2 (arm64). Times are medians of 5 runs in milliseconds; lower is better. The compiler was the working tree based on `7eb8e4e`, including roadmap items 1–5; the snapshot records its source hash and tool versions.

The [2026-10-10 Intel cloud snapshot](results/2026-10-10.json) is preserved for history, not used as a same-machine regression comparison. py2wasm was not installed for this run; its old measurements have not been carried forward.

### Strings fusion follow-up

After the full-suite snapshot, plain integer f-strings were changed to write digits directly into the final buffer. The strings workload now requests two GC allocations per formatted result instead of nine (excluding one-time literals and the occasional `+ "!"`). This removes about 4.2 million temporary allocations over 600,000 iterations. The optimized module grew from 1,865 to 2,025 bytes.

[Separate before/after results](results/2026-10-11-strings-fusion.json), with five interleaved repetitions of the preserved pre-change binary and a fresh build:

| Wasmtime host / collector | Before ms | After ms | Speedup |
|---|---|---|---|
| Python 49.0.0 binding, default collector | 555.9 | 136.4 | 4.07× |
| CLI 49.0.2, explicit DRC | 535.8 | 150.9 | 3.55× |
| CLI 49.0.2, explicit copying | 79.0 | 47.2 | 1.67× |

The Python row measures instantiation + execution of an already-compiled module, exactly as in the compute suite; this binding does not expose collector selection. CLI rows explicitly select the collector and include fresh process startup and uncached compilation, so compare before/after within each row, not across timing methods. All 30 executions matched CPython. Raw samples, module hashes, source hash and tool versions are saved in the follow-up JSON.

The fresh baseline is slower than the earlier snapshot's 374.5 ms, so the speedup above uses the interleaved baseline, not a cross-run ratio. The full-suite tables below remain the earlier, pre-fusion measurements; other workloads and runtimes have not been retimed after this optimization.

### Full-suite compute times (before string fusion)

| | fib | mandelbrot | binary_trees | sieve | strings | exceptions |
|---|---|---|---|---|---|---|
| snek (V8) | 2.0 | 2.9 | 9.2 | 5.2 | 21.9 | 0.9 |
| snek (Wasmtime) | 2.6 | 3.5 | 221.9 | 16.2 | 374.5 | 17.1 |
| CPython, native | 52.3 | 88.8 | 294.7 | 48.7 | 80.8 | 96.3 |
| Pyodide (V8) | 78.7 | 174.9 | 477.5 | 70.8 | 194.5 | 212.1 |
| CPython WASI (Wasmtime) | 232.4 | 458.4 | 2,175.3 | 226.5 | 525.1 | 733.1 |
| RustPython WASI (Wasmtime) | 683.6 | 831.2 | 4,390.6 | 397.0 | 556.4 | 718.9 |
| MicroPython (V8) | 657.0 | 1,132.0 | 2,961.0 | 470.0 | 889.0 | 1,527.0 |

### New language features

| | dicts | list_builtins | protocols | generators | async_calls |
|---|---|---|---|---|---|
| snek (V8) | 3.0 | 9.8 | 0.9 | 4.0 | 1.9 |
| snek (Wasmtime) | 18.9 | 18.6 | 3.1 | 14.6 | 10.0 |
| CPython, native | 13.9 | 40.1 | 48.0 | 17.5 | 22.3 |
| Pyodide (V8) | 30.0 | 71.4 | 80.9 | 28.9 | – |
| CPython WASI (Wasmtime) | 95.9 | 207.8 | 327.1 | 89.1 | – |
| RustPython WASI (Wasmtime) | 89.0 | 1,098.5 | 695.4 | 110.2 | – |
| MicroPython (V8) | 16,577.0 | 409.0 | 450.0 | 163.0 | – |

`–` means intentionally skipped; see the async caveat below. All 73 nonempty runtime/workload combinations measured here passed output checks on all five repetitions (plus seven empty-program baselines).

### Startup and size

"One-time compile" is the engine compiling the `.wasm` file. "Start" is instantiating the module and running an empty program, with the module already compiled. For Pyodide and MicroPython the loader does both at once. Node figures come from fresh processes but exclude launching Node itself. Wasmtime compile medians use three fresh processes; starts use five repetitions. Native CPython includes launching the process.

| | Module size | One-time compile | Start |
|---|---|---|---|
| snek (V8) | 0.3 KB | 0.1 ms | 0.6 ms |
| snek (Wasmtime) | 0.3 KB | 1.2 ms | 0.03 ms |
| CPython, native | – | – | 52 ms (whole process) |
| Pyodide (V8) | 13.4 MB | – | 719 ms |
| CPython WASI (Wasmtime) | 26.3 MB | 269 ms | 13 ms |
| RustPython WASI (Wasmtime) | 36.7 MB | 433 ms | 12 ms |
| MicroPython (V8) | 0.6 MB | – | 9.5 ms |

The empty core module is 291 bytes; the eleven core benchmark modules range from 558 to 3,616 bytes. Interpreter sizes above describe the runtime, not per-program snek output; Pyodide/MicroPython include their loader files.

### What it shows

- V8 is faster than native CPython on all eleven workloads in this run. The generator pipeline takes 4.0 ms versus 17.5 ms; tuple-key counting takes 3.0 ms versus 13.9 ms. These results apply to the typed workloads, not arbitrary Python.
- Wasmtime is close to V8 on recursive calls and float loops, but much slower on allocation-heavy code: binary trees take 221.9 ms versus 9.2 ms and strings 374.5 ms versus 21.9 ms. Strings and dictionaries are slower than native CPython on Wasmtime here.
- The async figures exercise the new state machines but compare different schedulers. They are not an `asyncio` replacement or concurrent-I/O benchmark.
- MicroPython's tuple-key dictionary workload is a large outlier (16.6 seconds). That is a result for this particular key/update pattern and build, not a claim about all dictionaries.

### Allocation probe

`bench.py alloc` compares three million small-object allocations into a ring buffer against field writes, with a separate live set. Every repetition's checksum is checked. The figures below are `(median allocation time − median field-write time) / 3,000,000`, in nanoseconds:

| Objects kept alive | Wasmtime | V8 |
|---|---|---|
| 0 | 47.3 | 2.4 |
| 100,000 | 47.2 | 1.5 |
| 300,000 | 43.5 | 2.3 |
| 1,000,000 | 16.4 | 6.4 |

This supports investigating GC/allocation overhead on Wasmtime, but does not isolate one collector mechanism. The live-set dependence is non-monotonic, and subtracting two short timings is noisy; these are workload-specific estimates, not universal per-object costs.

### WASI 0.3 components

Five fresh Wasmtime 49.0.2 CLI processes per program, with engine caching disabled. These are end-to-end wall times, including process startup and compilation; all 60 executions matched CPython output.

| Program | Median ms | Module KB |
|---|---|---|
| empty | 12.7 | 3.6 |
| fib | 14.4 | 3.9 |
| mandelbrot | 14.8 | 4.1 |
| binary_trees | 44.1 | 4.6 |
| sieve | 29.8 | 4.5 |
| strings | 67.9 | 4.7 |
| exceptions | 13.1 | 4.2 |
| dicts | 19.9 | 5.3 |
| list_builtins | 32.6 | 5.1 |
| protocols | 16.0 | 4.7 |
| generators | 26.3 | 5.6 |
| async_calls | 17.7 | 5.9 |

Some allocation-heavy component runs are faster even with compilation included than the wasmtime-py core runs above. The host, Wasmtime patch version (CLI 49.0.2 versus Python package 49.0.0), and generated wrapper differ. These results do not establish a component speedup or isolate its cause.

## Method and caveats

- **How time is measured:**
  - Interpreters time `main()` from inside the program (`time.perf_counter`, or `time.ticks_us` on MicroPython), so interpreter startup is excluded.
  - snek core modules are timed around instantiation and `_start`; engine compilation is excluded.
  - Components are timed around a fresh `wasmtime run -C cache=n` process. These numbers include CLI startup, uncached engine compilation, instantiation and execution, but not the snek source-to-Wasm build. They are not a measurement of component ABI overhead alone.
- **How engines run:**
  - WASI modules run in Wasmtime through `wasmtime-py`. Each module is compiled once per process and reused across repetitions.
  - Pyodide and MicroPython run in one Node process per runtime, so a single interpreter instance runs all repetitions.
  - No separate warmup is discarded: the first sample is retained. V8 tiering can make it slower than later samples. Raw samples are saved alongside medians.
- **Async is a limited comparison.** Native CPython uses `asyncio.run` and `sleep(0)`; snek core drives one coroutine locally, and snek components yield to the WASI callback scheduler. This tests nested awaits and checkpoints, not concurrent tasks or async I/O throughput. The harness skips `async_calls` on other Python runtimes because their event-loop integration is not configured here; it does not imply they lack async syntax.
- **Python versions differ:** CPython 3.12 (WASI build), 3.11 (optional py2wasm), 3.13 (native) and 3.14 (Pyodide). MicroPython uses the PyScript build, not a minimal standalone build.
- **The workloads suit snek.** They are statically typed programs within its supported subset. Results do not predict performance for arbitrary Python, standard-library-heavy code or dynamic features. Desktop background activity and CPU scheduling are not controlled.
- **Provenance:** every command records platform, CPU, tool versions, Git HEAD, dirty status, and a SHA-256 of compiler/runtime and benchmark sources, including uncommitted files. A new invocation rebuilds snek modules and replaces that command's work results instead of merging old rows. `snapshot` rejects incomplete runs and mismatched provenance. The historical cloud snapshot is left unchanged.
- **Validation:** reference failures stop a run; wrong output or runtime failures return nonzero. Failed measurements are omitted from tables. Missing runtimes and intentionally unsupported workload/runtime combinations are recorded as skips.

## Running it

Owned compiler phase profiling (diagnostic build, not a replacement compiler):

```sh
python bench/profile_compiler.py --collector drc --reps 5 --only tiny,medium,large --out bench/results/my-compiler-profile.json
python tests/check_compiler_profile.py
```

The current native compiler builds an instrumented copy of the linked source.
It records monotonic-clock intervals around I/O, decoding, declaration setup,
source/built-in/runtime parsing, body checking, WAT emission and binary encoding.
In lazy-runtime builds, `runtime_index` replaces `parse_runtime`: it measures
constructing the embedded source index. Parsing helpers that are actually used
is included in `check_bodies`, so the phase change does not imply that all helper
parsing work has disappeared.
Every output must match the native compiler's output byte-for-byte. One warmup
is discarded per input. `--only self` adds full compiler-bundle profiling.
The Wasmtime CLI collector is pinned to DRC by default to match the native
executable; do not compare its automatic collector selection with DRC results.
The probe still uses CLI JIT code, not the native executable's portable AOT image,
and instrumentation can perturb execution. Use the paired native benchmark for
end-to-end performance claims. Failed runs remain marked incomplete.

Standalone/compiler follow-up (requires the native executable and matching
local packages built as described in `packaging/README.md`, plus Wasmtime's
Python package, Node, Binaryen, npm, Bun and uv):

```sh
python bench/native_bench.py --reps 5 --self-reps 3 --out bench/results/my-native-run.json
python bench/native_bench.py --sections startup,packages --reps 10 --out bench/results/my-launchers-run.json
# Save the old executable before rebuilding, then compare both in one run:
python bench/native_bench.py --baseline /path/to/old-snek --sections compile,run --reps 10 --self-reps 3 --out bench/results/my-native-comparison.json
python tests/check_native_bench.py
native/target/release/snek --timings build examples/hello.py -o /tmp/hello.wasm
```

The harness refuses to overwrite snapshots. `--sections` also accepts `compile`,
`run` and `compute`. It requires macOS/Linux for `wait4` memory accounting, uses
temporary package installs/caches, validates every sample, checks that source
files did not change during the run, and saves partial failures as incomplete.
For package-manager process trees RSS is the OS child high-water statistic, not
the sum of concurrent resident processes; compute-worker RSS includes JIT and
all iterations, unlike its execution timer. Phase timings are opt-in JSON on
stderr; source files, compiled artifacts and guest stdout are unchanged.
With `--baseline`, the compile/run sections compare only the two native binaries,
record both executable hashes, and require byte-identical compilation outputs
(including the verified stage-3 compiler). Other sections reject this option.

Original multi-runtime harness:

```sh
cd bench && npm ci && cd ..                # pinned interpreter runtimes
python -m pip install wasmtime             # optional unless measuring Wasmtime core modules
export SNEK_NODE=/path/to/node               # Node 24+ (defaults to `node`)
export SNEK_WASM_OPT="npx wasm-opt"          # if wasm-opt isn't on PATH
export PY2WASM=/path/to/venv/bin/py2wasm     # optional: needs Python 3.11 and patchelf in that venv
# Components also need wasm-tools 1.250+ and the Wasmtime CLI 49+ on PATH.
# SNEK_WASM_TOOLS and SNEK_WASMTIME can select their executables.

python bench/bench.py run                    # compute times -> bench/work/results.json
python bench/bench.py startup                # startup and sizes -> bench/work/startup.json
python bench/bench.py alloc                  # allocation cost, Wasmtime vs V8 -> bench/work/alloc.json
python bench/bench.py component              # cold WASI 0.3 CLI times -> bench/work/component.json
python bench/bench.py snapshot               # -> bench/results/<date>.json
python bench/bench.py table                  # print the tables above from the latest snapshot
python tests/check_benchmarks.py             # harness regressions and flat-source equivalence
```

For a controlled strings optimization comparison, preserve the old binary **before** making the compiler change:

```sh
cp bench/work/strings.snek.wasm bench/work/strings.before.wasm
# After changing the compiler:
python bench/compare_strings.py --before bench/work/strings.before.wasm --reps 5
# Writes bench/work/strings-comparison.json; --out selects another destination.
```

- `run --runtimes snek-v8,cpython-native --only fib,strings --reps 3` narrows a run.
- `component --only generators,async_calls --reps 3` narrows a component run.
- Use CPython 3.13 to reproduce the current reference; `python` above is the interpreter being measured.
- A filtered run replaces the command's default work file too. Use `--out bench/work/experiment.json` to keep a separate experiment; `snapshot` reads only the four standard work filenames.
- Runtimes that aren't installed are skipped, with a note saying why.
