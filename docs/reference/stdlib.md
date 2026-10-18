---
source_doc: README.md
source_sections:
  - Standard-library streams
  - Host configuration, clocks, and randomness
---

# Standard library

Snek's library namespaces are recognized by the compiler. They expose typed
operations over WASI and provide CPython equivalents for testing.

| Namespace | Available APIs |
| --- | --- |
| `snek.sys` | `argv`, `stdin`, `stdout`, `stderr` |
| `snek.io` | Typed stream annotations, including text-output and binary-input/output views |
| `snek.os` | `getenv(name, default)` with explicit guest environment grants |
| `snek.time` | `time()`, `monotonic()`, async `sleep(seconds)` |
| `snek.secrets` | `token_bytes(n)`, `token_hex(n)`, `randbelow(n)` |
| `snek` | Machine types, `run`, `checkpoint`, structured `wait_all` / `gather`, and core-only `read_bytes` / `write_bytes` |

This is not the full CPython standard library. Filesystem access, HTTP, sockets,
text stdin, and a general package loader are not implemented.

## Copy stdin in bounded chunks

<!-- example: streams.snek -->

From the checkout, on a POSIX shell:

```sh
printf 'hello\n' | python -m snekc run docs/examples/streams.snek --no-opt
printf 'hello\n' | python -m snekc run docs/examples/streams.snek --component --no-opt
```

`read(4096)` returns up to 4,096 bytes, filling the request unless EOF arrives.
Draining every chunk bounds queued output. The component route uses async WASI
streams; the core route exposes the same source API over blocking preview1 I/O.
