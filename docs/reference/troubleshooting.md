# Troubleshooting

## `wasm-opt` is missing

Default development builds optimize output. Install Binaryen, point
`SNEK_WASM_OPT` at its command, or pass `--no-opt` to `build` / `run`.
`check` and `wat` do not need an optimizer. The native CLI produces unoptimized
output without an external optimizer.

## The self-hosted compiler is missing or stale

Run `python bootstrap/bootstrap_compiler.py` from the repository root, then
`python tests/run_suites.py compiler`. Rebuild after changing compiler or embedded
runtime sources. Missing or failed compiler images do not fall back to Python.
To deliberately use the reference pipeline, pass `--compiler python`.

## A component will not run in Node or Python Wasmtime

Those runners support the core target only. Install the Wasmtime CLI 49+ and
`wasm-tools` 1.250+, use `--component`, and unset a forced `SNEK_RUNTIME` or set
it to `wasmtime`. A core `.wasm` and a component `.wasm` have the same extension
but different interfaces.

## Node is installed, but the runner rejects it

Use Node 24 or newer. Node 22 has a Wasm exception-handling issue affecting
optimized code; Snek refuses it. `SNEK_NODE` can select another executable.

## `int`, truthiness, or optional numbers are rejected

Import machine types such as `i32` and `f64` from `snek`. Write explicit bool
conditions. Only reference types can be optional; use a separate flag or an
appropriate reference wrapper when absence is meaningful for a number.

## A field or captured variable does not narrow

Copy it into a local, then check the local. Other aliases or closures can mutate
the original value; narrowing shared state would be unsound. See
[reference types](../language/types.md#reference-types-and-none) and
[closures](../language/functions.md#closures-and-callable-values).

## `os.getenv()` cannot see my shell variables

Guest environments are intentionally empty by default. Grant exactly what the
program needs:

```sh
python -m snekc run program.snek --no-opt --env MODE=demo
```

The CPython shim uses the ordinary Python process environment, so a direct Python
run can see variables that a Wasm guest cannot.

## Cleanup did not run after leaving a generator loop

Breaking out of a loop does not close its generator. Explicitly call `close()`
when abandoning a generator whose suspended `with` or `finally` owns resources.
WasmGC does not emulate CPython's refcount-triggered finalization.

## My Python dependency does not import

Local source modules and packages work when their code is in Snek's supported
subset. Imports are resolved under the entry directory, or `--module-root PATH`,
not Python's installed packages. Check that packages contain `__init__.py` or
`__init__.snek`, and that a module does not have competing `.py` and `.snek`
files. Import cycles, wildcard imports and dynamic imports are rejected. An
annotation does not make an arbitrary Python dependency compatible with Snek.
See [modules and imports](../language/modules.md).

## Report a compiler problem

Open an [issue](https://github.com/rickfast/snek/issues) with a minimal program,
the exact command, expected and actual output, commit SHA, OS, Python version,
compiler selection, runtime, and optimizer/tool versions. State whether it
reproduces with `--no-opt`, `--compiler python`, and `--compiler snek` when available.
Never include credentials or private environment values.
