# Your first program

After [installation](installation.md), save this as `hello.snek` in your checkout,
or use the existing `docs/examples/hello.snek`:

<!-- example: hello.snek -->

## What the types mean

`i32` is a signed, fixed-width 32-bit integer. Function parameters and return
values are annotated. `total` and the loop variable are inferred as `i32`.
`range(1, n + 1)` excludes its upper bound, just as in Python.

Snek does not use Python's unbounded `int` type. Arithmetic that overflows an
`i32` wraps, so choose `i64` when a larger fixed range is needed. See
[types and numbers](../language/types.md) for conversion rules.

## Check it

```sh
python -m snekc check hello.snek
```

The command reports `hello.snek: ok` on success. Checking does not require
Binaryen or a Wasm engine when using the default Python reference compiler.

## Run it

```sh
python -m snekc run hello.snek --no-opt
```

```text
Hello, WebAssembly!
55
```

The CLI compiles to a temporary Wasm module, executes it, and forwards its output
and exit status. This is not execution of the source under CPython.

## Keep the binary

```sh
python -m snekc build hello.snek --no-opt -o hello.wasm
```

The resulting core module needs a Wasm engine with WasmGC, exception handling,
and WASI support. With the Wasmtime CLI installed:

```sh
wasmtime run hello.wasm
```

For a readable view of what the compiler emitted:

```sh
python -m snekc wat hello.snek
python -m snekc build hello.snek --no-opt --keep-wat
```

## Compare with Python

With the local `snek` shim installed, CPython can execute the same file:

```sh
python hello.snek
```

This is useful for checking expected behavior. It is not a guarantee that machine
integer overflow or every runtime edge case matches Python. The
[compatibility guide](python.md) explains those differences.

Next: [build a word counter](word-counter.md).
