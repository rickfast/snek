# snek benchmarks

How snek compares with the other ways of running Python as WebAssembly, with native CPython as the reference. Every benchmark is a single snek source file, which is also plain Python, so the same code runs on every runtime. Each runtime's output is checked against native CPython before its time counts.

## Runtimes

| Runtime | What it is | Engine |
|---|---|---|
| snek (V8) | This compiler's output: WasmGC + Wasm exception handling | Node 24 |
| snek (Wasmtime) | The same `.wasm` file | Wasmtime 49 |
| py2wasm | Wasmer's ahead-of-time compiler: Nuitka C plus a bundled CPython 3.11, built for WASI | Wasmtime 49 |
| CPython, native | CPython 3.13 on Linux, the reference | native |
| Pyodide | CPython 3.14 built with Emscripten (`pyodide` 314.0.7) | Node 24 |
| CPython WASI | VMware Labs' CPython 3.12 WASI build (`@antonz/python-wasi`) | Wasmtime 49 |
| RustPython WASI | RustPython built for WASI (`rustpython-wasi`) | Wasmtime 49 |
| MicroPython | MicroPython 1.29, PyScript build with Emscripten | Node 24 |

## Benchmarks

| | Exercises |
|---|---|
| `fib` | Recursive calls and integer arithmetic (`fib(30)`) |
| `mandelbrot` | Tight floating-point loops (200×200, 100 iterations) |
| `binary_trees` | Allocation and garbage collection (the benchmarks-game program, depth 14) |
| `sieve` | List appends and indexing (`list[bool]`, 5 × 200,000) |
| `strings` | f-strings, concatenation and comparison (600,000 strings) |
| `exceptions` | Raising and catching a user exception class (1,000,000 iterations, one in three raises) |

## Results

From [`results/2026-10-10.json`](results/2026-10-10.json), on a 2-core Intel Xeon cloud VM. Times are medians of 5 runs in milliseconds; lower is better.

| | fib | mandelbrot | binary_trees | sieve | strings | exceptions |
|---|---|---|---|---|---|---|
| **snek (V8)** | **5** | **7** | **37** | **13** | **99** | **7** |
| **snek (Wasmtime)** | **8** | **7** | 502 | 24 | 898 | 39 |
| py2wasm (Wasmtime) | 154 | 391 | 3,192 | 273 | 652 | 443 |
| CPython, native | 102 | 205 | 713 | 122 | 205 | 231 |
| Pyodide (V8) | 160 | 320 | 1,172 | 157 | 489 | 589 |
| CPython WASI (Wasmtime) | 516 | 1,409 | 7,698 | 673 | 1,858 | 3,399 |
| RustPython WASI (Wasmtime) | 1,885 | 2,005 | 13,972 | 1,295 | 1,592 | 2,682 |
| MicroPython (V8) | 1,994 | 2,952 | 8,410 | 1,302 | 2,166 | 4,234 |

### Startup and size

"One-time compile" is the engine compiling the `.wasm` file, which caching makes a one-off in practice. "Start" is instantiating the module and running an empty program, with the module already compiled. For Pyodide and MicroPython the loader does both at once.

| | Module size | One-time compile | Start |
|---|---|---|---|
| snek (V8) | 0.3 KB | 0.4 ms | 1.1 ms |
| snek (Wasmtime) | 0.3 KB | 3.4 ms | 0.1 ms |
| py2wasm (Wasmtime) | 25.6 MB | 3.1 s | 141 ms |
| CPython, native | – | – | 14 ms (whole process) |
| Pyodide (V8) | 13.4 MB | – | 2.2 s |
| CPython WASI (Wasmtime) | 26.3 MB | 4.1 s | 47 ms |
| RustPython WASI (Wasmtime) | 36.7 MB | 7.8 s | 35 ms |
| MicroPython (V8) | 0.6 MB | – | 24 ms |

- The empty program compiles to 0.3 KB; the benchmark modules are 0.5–2 KB.
- py2wasm bundles CPython into every program, so each one is about 25.6 MB.

### What it shows

- **On V8, snek is 9–60x faster than native CPython.** That holds for calls, float loops, allocation and exceptions. Against Pyodide on the same engine it's 11–150x faster.
- **On Wasmtime, plain computation runs as fast as on V8.** Allocation-heavy code is 10–15x slower than on V8, because of Wasmtime's garbage collector rather than snek's output. [`bench.py alloc`](bench.py) isolates it:

  | Objects kept alive | ns per allocation, Wasmtime | ns per allocation, V8 |
  |---|---|---|
  | 0 | 124 | 8 |
  | 100,000 | 108 | 10 |
  | 300,000 | 109 | 16 |
  | 1,000,000 | 59 | 36 |

  The cost doesn't grow with the live set, so it's a fixed overhead per allocation or collection rather than copying live data. Code that only reads and writes object references is also 2–6x slower on Wasmtime. GC support has only been on by default in Wasmtime since July 2026 (v47), so this should improve.
- **`strings` is snek's weakest benchmark.** Each f-string allocates its parts and the result, which is cheap on V8 but costly on Wasmtime. The next step is to format numbers straight into the result's buffer, so an f-string allocates once.
- **Among the other WASI Pythons, py2wasm is closest to native CPython.** Compiling with Nuitka removes some interpreter overhead, but every program carries a full CPython. The CPython WASI build runs 3–15x slower than native CPython.

## Method and caveats

- **How time is measured:**
  - Interpreters time `main()` from inside the program (`time.perf_counter`, or `time.ticks_us` on MicroPython), so interpreter startup is excluded.
  - snek has no clock API, so its time is measured around `_start` and includes instantiating the module (about 1 ms).
- **How engines run:**
  - WASI modules run in Wasmtime through `wasmtime-py`. Each module is compiled once per process and reused across repetitions.
  - Pyodide and MicroPython run in one Node process per runtime, so a single interpreter instance runs all repetitions.
- **Python versions differ:** CPython 3.12 (WASI build), 3.11 (py2wasm), 3.13 (native) and 3.14 (Pyodide).
- **The MicroPython package is the PyScript build**, which probably adds overhead for JavaScript interop. A plain MicroPython build would likely do better.
- **The machine was small:** 2 cores, with nothing else running during measurement.
- **The workloads suit snek.** They are statically typed programs within the subset snek supports. Code that leans on dicts, the standard library or dynamic features can't be compared yet.
- **Provenance of the snapshot:** the compute numbers come from a full 5-repetition run. The two snek rows were re-run after the string-building change (`abd8f81`). Startup and allocation numbers were measured at that same commit.

## Running it

```sh
cd bench && npm install && cd ..           # Pyodide, CPython WASI, RustPython, MicroPython
pip install wasmtime                         # Wasmtime for the WASI modules
export SNEK_NODE=/path/to/node               # Node 24+ (defaults to `node`)
export SNEK_WASM_OPT="npx wasm-opt"          # if wasm-opt isn't on PATH
export PY2WASM=/path/to/venv/bin/py2wasm     # optional: needs Python 3.11 and patchelf in that venv

python bench/bench.py run                    # compute times -> bench/work/results.json  (about 15 min)
python bench/bench.py startup                # startup and sizes -> bench/work/startup.json
python bench/bench.py alloc                  # allocation cost, Wasmtime vs V8 -> bench/work/alloc.json
python bench/bench.py snapshot               # -> bench/results/<date>.json
python bench/bench.py table                  # print the tables above from the latest snapshot
```

- `run --runtimes snek-v8,cpython-native --only fib,strings --reps 3` narrows a run.
- Runtimes that aren't installed are skipped, with a note saying why.
