# CLI reference

Snek has a standalone native CLI (`snek`) and a Python development CLI
(`python -m snekc`, also installed as `snekc`). Both accept `.snek` and `.py` input.

## Standalone `snek`

Build the executable using the [installation guide](../learn/installation.md).
It embeds the verified self-hosted compiler and Wasmtime.

```sh
snek check program.snek
snek wat program.snek
snek build program.snek -o program.wasm
snek run program.snek -- first second
snek run program.snek --env MODE=demo -- first second
snek --timings build program.snek
```

| Command / option | Behavior |
| --- | --- |
| `check SOURCE` | Compile and discard output; report errors without creating a binary |
| `wat SOURCE` | Write generated WebAssembly text to stdout |
| `build SOURCE [-o PATH]` | Write an unoptimized core Wasm module |
| `run SOURCE [--env NAME[=VALUE]] -- [ARGS...]` | Compile and execute with embedded Wasmtime |
| `--timings` | Emit a `SNEK_TIMINGS` JSON record on stderr |
| `--module-root PATH` | Resolve local imports under this root; defaults to the entry file's directory |
| `--version`, `--help` | Version and command help |

The native CLI does not currently support `--component`, `--no-opt`, or
`--compiler`. It needs no external optimizer because it does not optimize output.

## Development CLI

```sh
python -m snekc check program.snek
python -m snekc wat program.snek
python -m snekc build program.snek -o program.wasm --no-opt --keep-wat
python -m snekc run program.snek --no-opt -- first second
python -m snekc build program.snek --component
```

| Option | Commands | Purpose |
| --- | --- | --- |
| `--compiler python\|snek` | All | Reference compiler (default) or independent self-hosted compiler |
| `--frontend cpython\|snek` | All | Parser selection within the Python reference pipeline |
| `--module-root PATH` | All | Explicit local module/package root |
| `-o PATH`, `--out PATH` | `build` | Explicit output path |
| `--no-opt` | `build`, `run` | Skip Binaryen; encode core Wasm directly |
| `--keep-wat` | `build` | Keep readable WAT beside the binary |
| `--component` | `build`, `run` | Build/run a native WASI 0.3 command component |
| `--env NAME[=VALUE]` | `run` | Grant one guest environment variable; repeat as needed |

`build` replaces the input's extension with `.wasm` by default, beside the source file.
Component builds also write a `.wit` file describing the imports and exports.
`run` uses a temporary output directory. Put guest arguments after `--` to
distinguish them from compiler options.

For a package entry point, use e.g. `snek run app/main.py --module-root .`.
Module projects use the shared Snek-owned parser/linker with either compiler;
`--frontend` retains its existing meaning for standalone sources and reference
runtime helpers. See [module rules](../language/modules.md).

!!! note "Parser selection is not compiler selection"
    `--frontend snek` uses the Snek-written parser through the Python reference
    pipeline. `--compiler snek` runs the independent Wasm compiler and does not
    use the Python checking/emission passes. Bootstrap it first:

    ```sh
    python bootstrap/bootstrap_compiler.py
    python -m snekc run program.snek --compiler snek --no-opt
    ```

## Environment variables

These configure the development tools, not the program's guest environment.

| Variable | Meaning |
| --- | --- |
| `SNEK_COMPILER` | Default compiler: `python` or `snek` |
| `SNEK_FRONTEND` | Default reference parser: `cpython` or `snek` |
| `SNEK_COMPILER_WASM` | Override the self-hosted compiler image |
| `SNEK_RUNTIME` | Execution engine: `wasmtime-py`, `wasmtime`, or `node` |
| `SNEK_NODE` | Node executable path |
| `SNEK_WASM_OPT` | Optimizer command, for example `npx wasm-opt` |
| `SNEK_WASM_TOOLS` | Component assembler/validator command |

Explicit compiler/frontend flags take precedence over their environment defaults.
Components require `SNEK_RUNTIME=wasmtime` if you have explicitly selected a
different engine. The standalone native executable does not use these Python
driver overrides.

## I/O, errors, and guest permissions

Both CLIs preserve raw stdin/stdout/stderr bytes and propagate guest exit codes.
Compilation errors do not replace an existing output file. Guest environment
variables are hidden by default; `--env NAME` inherits only that named variable,
and `--env NAME=VALUE` supplies an explicit value. There are no directory preopens.

The development CLI returns `1` for a source compile error and `2` for a tool,
configuration, or file-access error. Native errors also return a nonzero status;
do not assume its diagnostic text is identical to the Python reference compiler.
