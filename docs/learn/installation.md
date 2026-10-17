# Installation

!!! warning "Start with a checkout"
    The npm and PyPI release packages are named `snek-lang`, but they are not
    published yet. Do not rely on `npx snek-lang` or `uvx snek-lang` until a release
    is announced. These instructions work from source today.

## Development CLI

Use Python 3.12 or newer; the project's CI uses Python 3.13.14. Run these commands
in a terminal with Git and Python installed:

```sh
git clone https://github.com/rickfast/snek.git
cd snek
python -m venv .venv
```

Activate the environment:

=== "macOS / Linux"

    ```sh
    source .venv/bin/activate
    ```

=== "Windows PowerShell"

    ```powershell
    .venv\Scripts\Activate.ps1
    ```

Install the local development package and a core Wasm runtime:

```sh
python -m pip install -e ".[run]"
python -m snekc run docs/examples/hello.snek --no-opt
```

Expected output:

```text
Hello, WebAssembly!
55
```

`--no-opt` bypasses Binaryen. Core binary encoding itself needs only Python;
executing the result needs a runtime. The `run` extra installs Python Wasmtime.
You can instead use Node 24+ or a compatible Wasmtime CLI.

## Optional optimizer

Default `build` and `run` commands invoke Binaryen's `wasm-opt`. With npm installed:

```sh
npm install -g binaryen@132.0.0
python -m snekc build docs/examples/hello.snek -o hello.wasm
```

Skip this dependency by continuing to use `--no-opt`. See the
[CLI environment variables](../reference/cli.md#environment-variables) if tools
are not on your `PATH`.

## Standalone executable

Install the pinned Rust 1.96.0 toolchain and either Node 24+ or Python Wasmtime.
From the checkout, run:

```sh
rustup toolchain install 1.96.0 --profile minimal
python packaging/build_native.py
native/target/release/snek run docs/examples/hello.snek
```

On Windows, the resulting executable is `native/target/release/snek.exe`.
The build helper regenerates the compiler, verifies its fixed point, and runs
the Rust build and tests. Subsequent uses of that executable need neither Python
nor an external Wasmtime installation. It currently produces unoptimized core Wasm.

See [native distributions](../reference/distribution.md) for the supported
platforms, offline package tests, and release boundary.

## Components

Install the Wasmtime CLI 49+ and `wasm-tools` 1.250+ for WASI 0.3 component output.
The CI pins Wasmtime 49.0.2 and wasm-tools 1.250.0. Those tools are in addition
to the development CLI; the Python Wasmtime package cannot run components.

```sh
python -m snekc build docs/examples/streams.snek --component --no-opt -o streams.wasm
```

This writes `streams.wasm` and `streams.wit`. Continue with
[targets and runtime selection](../reference/targets.md).
