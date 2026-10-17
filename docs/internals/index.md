---
source_doc: README.md
source_sections:
  - Pipeline
  - Layout
---

# Compiler architecture

Snek has two compiler implementations and a small native launcher:

- **`snekc/`** is the Python reference compiler, development CLI, runtime, and host integration.
- **`bootstrap/`** contains the independent compiler written in Snek, plus host build tools and pass-level probes.
- **`native/`** embeds the verified compiler and Wasmtime in a Rust executable. It does not reimplement compiler passes.

The independent compiler owns source parsing, type checking, closure and
suspension lowering, runtime generation, WAT emission, and core binary encoding.
The [self-hosting proof](self-hosting.md) disables host compiler entry points
while it builds stages 2 and 3.

## Shared source, separate passes

Built-in exceptions, string helpers, and formatting helpers are authored in
`snekc/runtime/*.snek`. The resource loader resolves private Unicode constants;
bootstrap preparation embeds their source. Helpers are parsed lazily per
compilation. The standalone compiler does not read those files at runtime.

`snekc/runtime_abi.py` is a Snek-compatible source of import contracts, consumed
by both backends. Algorithms remain separate, so differential tests can compare
their behavior. Temporary `BodyContext` state is kept separate from completed
`BodyFunction` IR, and emitting a module does not mutate its entry function.

## Why are the compiler sources still `.py`?

Dual-use Snek compiler sources run under CPython with the shim for seed
construction and parity tests. Python's normal module importer expects `.py`.
Snek-only library sources use `.snek`; GitHub highlights them as Python.
