# Standalone Snek distributions

Package name: **snek-lang** on npm and PyPI. Command: **snek**, with a
**snek-lang** alias for package-manager auto-discovery.

These packages are built locally and in CI; they have **not been published**.
Once published, the commands will be:

```sh
npx snek-lang run hello.py
bunx --bun snek-lang run hello.py
uvx snek-lang run hello.py

# Explicit package/command selection also works:
npx --package=snek-lang snek build hello.py -o hello.wasm
bunx --bun --package snek-lang snek check hello.py
uvx --from snek-lang snek run hello.py -- arg1 arg2

# Persistent installs:
npm install -g snek-lang
bun install -g snek-lang
uv tool install snek-lang
snek run hello.py
```

`bunx --bun` runs the tiny JavaScript launcher with Bun, without needing Node
for its `node` shebang. Plain `bunx snek-lang` also works when Node is present.
The native compiler needs neither Node, Python nor an external Wasmtime;
`uvx` uses Python only for its package entry point.

## What is bundled

`native/` builds one executable containing the verified stage-3 compiler and
Wasmtime. The build checks the bootstrap manifest and stage hashes, source-bundle
hash, and equality of stage 2, stage 3 and the published compiler. It does not
implement any compiler passes in Rust. Only command dispatch, file handling,
runtime setup and diagnostics live there.

After verification, the Rust build precompiles stage 3 into a Wasmtime native
image for Cargo's `TARGET`, with host CPU-feature inference disabled. The image
is embedded directly in the executable: each invocation deserializes it instead
of JIT-compiling the compiler again. Build and runtime share Wasm configuration
and the pinned Wasmtime version. Only these immutable, build-produced bytes are
deserialized; user programs still use Wasmtime's normal validated compilation.
There is no writable native-code cache or compiler-image override. The CI matrix
builds on each target's native architecture; cross-compilation is not tested.

The first standalone release supports `build`, `run`, `check` and `wat` for core
Wasm. It uses direct source-to-binary compilation. `check` compiles and discards
the output. There is no optimization or component packaging in this CLI yet;
use the existing `python -m snekc` development CLI for those features.
Guest stdin/stdout/stderr and exit status are forwarded. Environment variables
are hidden unless granted with `snek run --env NAME[=VALUE]`; no directories are
preopened. Compilation errors do not replace an existing output.

For profiling, `snek --timings build hello.py` (or `run`) emits a
`SNEK_TIMINGS` JSON record on stderr, separating engine setup, compiler loading,
compiler execution and guest execution. It is off by default. See
`bench/native_bench.py` for repeated wall-time, memory and package-launch tests.
`compiler_load_ms` now measures loading the precompiled image, not compiler JIT.

The npm package has exact-version optional dependencies on OS/CPU packages;
the JavaScript entry point selects one and runs its executable. Each Python
wheel contains that same executable and a minimal launcher. Both expose `snek`
and `snek-lang`. Neither format uses install scripts or runtime downloads.
Do not install npm packages with optional dependencies disabled.

Configured build targets:

| Platform | Native target | Minimum packaged OS/libc |
| --- | --- | --- |
| macOS Apple Silicon | aarch64-apple-darwin | macOS 11 |
| macOS Intel | x86_64-apple-darwin | macOS 11 |
| Linux x64 | x86_64-unknown-linux-gnu | glibc 2.35 |
| Linux ARM64 | aarch64-unknown-linux-gnu | glibc 2.39 |
| Windows x64 | x86_64-pc-windows-msvc | Windows 10+ |

Linux musl/Alpine and Windows ARM64 are not currently packaged. The CI workflow
builds each target on its native architecture and validates/repairs Linux wheels
with auditwheel. The Windows target statically links the C runtime via
`.cargo/config.toml`. Platform availability is not a claim that the hosted jobs have
already passed: all target jobs must pass before publishing.

## Build and test locally

Run from the repository root. Build tools are Python 3.13, Rust 1.96.0 and a
bootstrap runtime (Node 24+ or Wasmtime); users of the resulting executable do
not need them. `Cargo.lock` pins Rust dependencies. The commands below use the
Apple Silicon target; choose the matching target from the table on other hosts.

```sh
SNEK_RUNTIME=node python bootstrap/bootstrap_compiler.py
MACOSX_DEPLOYMENT_TARGET=11.0 cargo +1.96.0 build --manifest-path native/Cargo.toml --release --locked
cargo +1.96.0 test --manifest-path native/Cargo.toml --release --locked
native/target/release/snek run examples/hello.py
python tests/check_native.py --corpus
python packaging/build_packages.py --binary native/target/release/snek --target aarch64-apple-darwin
python tests/check_packages.py
```

The package test needs npm/npx, Bun and uv. It verifies wheel RECORD hashes,
executable permissions, and npm/wheel executable identity, installs the local
archives into temporary directories **offline**, and checks version reporting,
execution, Unicode arguments, binary stdin, build output, compile failures and
exit-code propagation through all three entry points. It also checks that failed
compiles preserve an existing output. Caches and installs stay in its temporary
directory; no global packages are changed. Offline testing installs the matching
platform tarball explicitly because it is not on npm yet.

`tests/check_native.py` copies the executable out of the repository and clears
PATH for its isolation tests. It also rebuilds the compiler bundle and compares
the result byte-for-byte with stage 3. `--corpus` adds all examples and negative
fixtures, with CPython as the behavioral reference.

The root `pyproject.toml` remains the Python reference/development package. It is
**not** the PyPI release artifact. Publish only the platform wheels produced by
this packaging workflow, not a wheel from the root setuptools project.

## Release boundary

`.github/workflows/packages.yml` only builds, tests and uploads artifacts. It has
read-only repository permissions and no publish steps or registry credentials.

Before a public release:

1. Pass all five native package jobs, including the installed-package tests.
2. Confirm ownership of `snek-lang` on npm/PyPI and all five npm platform names
   (`snek-lang-darwin-arm64`, `snek-lang-darwin-x64`, `snek-lang-linux-x64`,
   `snek-lang-linux-arm64`, `snek-lang-win32-x64`).
3. Choose the project's distribution license and include required third-party
   license notices. Review macOS signing/notarization and Windows signing needs.
4. Publish all platform npm packages at the same version **before** the npm
   launcher; publish all platform Python wheels together. The version source is
   `native/Cargo.toml`. Update the Cargo lockfile when changing that version.
5. Smoke-test registry-backed `npx`, `bunx` and `uvx` in clean environments, then
   remove the unpublished notice above.

Publishing, account setup and signing require a separate explicit release step.
