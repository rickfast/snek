---
source_doc: DESIGN.md
source_sections:
  - The WASI boundary
---

# WebAssembly targets

The core target is the default. Components are opt-in and require additional
host tools, but use the same Snek source language.

| | Core module | Native component |
| --- | --- | --- |
| Build | `python -m snekc build program.snek` | Add `--component` |
| Host interface | `wasi_snapshot_preview1` | `wasi:cli@0.3.0` |
| Run with | Node 24+, Python Wasmtime, or Wasmtime CLI | Wasmtime CLI 49+ |
| Extra build tool | None with `--no-opt` | `wasm-tools` 1.250+ even with `--no-opt` |
| Outputs | `.wasm` | `.wasm` and `.wit` |
| Native `snek` CLI | Supported | Not yet supported |

```sh
python -m snekc run examples/async_functions.py --component --no-opt
```

Do not load the default core module into a bare browser `WebAssembly.instantiate`
and expect it to run: it imports WASI functions and requires the relevant Wasm
features. A browser runner is not included in this documentation site.

The following notes describe the ABI, ownership, and cancellation behavior of
the component implementation. For the source-level APIs, see the
[standard library](stdlib.md).
