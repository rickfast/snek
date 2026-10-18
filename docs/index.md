---
title: Python syntax. WebAssembly underneath.
description: Snek is a statically typed Python subset with a self-hosted compiler, WasmGC objects, and WASI I/O.
hide:
  - navigation
  - toc
---

<div class="snek-hero" markdown>
<div markdown>
<span class="snek-kicker">The Snek programming language</span>

# Python syntax.<br>WebAssembly underneath.

Write familiar, typed Python. Compile it to WebAssembly with machine numbers,
garbage-collected objects, and catchable exceptions. No Python interpreter in
your compiled program.

[Start building](learn/installation.md){ .md-button .md-button--primary }
[Try it in your browser](learn/playground.md){ .md-button }
[Read the language guide](language/index.md){ .md-button }
</div>
<img src="logo.png" alt="A blue snake and a yellow snake playing together in a wooden sandbox" width="640" height="512">
</div>

## Small language. Real programs.

Snek supports classes, closures, Unicode strings, typed collections, generators,
and async functions. Types are checked ahead of time; objects live on the WasmGC
heap. The compiler itself is written in Snek and reproduces its own binary.

<!-- example: hello.snek -->

```sh
python -m snekc run docs/examples/hello.snek --no-opt
```

That prints `Hello, WebAssembly!` and `55`. Follow the [installation guide](learn/installation.md)
to set up a checkout and runtime first.

<div class="snek-paths" markdown>
<div markdown>
### Learn by writing
Build your first program, then a word counter with typed dictionaries and Unicode strings.

[Take the tutorial →](learn/first-program.md)
</div>
<div markdown>
### Find a language rule
Look up numeric conversions, optional references, class initialization, collection operations, and cleanup.

[Open the reference →](language/types.md)
</div>
<div markdown>
### Look inside the compiler
Follow source through checking and lowering to Wasm, then reproduce the self-hosting fixed point.

[Explore the internals →](internals/index.md)
</div>
</div>

## Choose your output

| | Core WebAssembly | WASI components |
| --- | --- | --- |
| Command | `python -m snekc build program.snek` | Add `--component` |
| Host | Node 24+, Python Wasmtime, or Wasmtime CLI | Wasmtime CLI 49+ |
| I/O boundary | WASI preview1 | Native WASI 0.3 streams and futures |
| Program data | WasmGC objects | Private WasmGC objects; linear-memory component boundary |

The [standalone native executable](reference/distribution.md) embeds the self-hosted
compiler and Wasmtime. It builds and runs core modules without Python or a separate engine.

<div class="snek-status" markdown>
**Current status:** self-hosting is implemented and native packages are built and tested locally.
The `snek-lang` npm and PyPI packages are **not published yet**. Start from a source checkout;
the installation guide does not assume a registry release. Snek is a restricted language,
not a way to compile arbitrary Python packages. See [Python compatibility](learn/python.md).
</div>
