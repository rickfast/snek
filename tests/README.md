# Test suites

The `browser` suite uses `tests/requirements-browser.txt` and Playwright browser
downloads. Set `SNEK_BROWSER=chromium`, `firefox`, or `webkit` (default Chromium).
It tests both compilers' raw/optimized libraries, typed host imports, the DOM
callback example, and the module-worker example in
a fresh local HTTP server and isolated browser contexts. Missing browsers and
unsupported Wasm features fail; there are no engine skips. See the
[browser guide](../docs/reference/browsers.md) for setup.

The `bundler` suite additionally requires `npm ci --prefix examples/vite`. It
copies that example to a temporary workspace, type-checks generated declarations
(including expected type errors), and runs Vite dev and production servers.
Both compilers, raw/optimized output, subdirectory and relative deployment bases,
async fetch, Unicode, bigint and bundled workers run in the selected browser.
The browser CI matrix runs this suite in Chromium, Firefox and WebKit as well.

The `playground` suite needs the verified compiler bootstrap, documentation
dependencies and a Playwright engine. It stages the compiler, builds a temporary
documentation site under `/snek/`, and executes the real editor and worker.
Tests cover every preset, stdin, diagnostics, output/source limits, Stop and
timeout recovery, Wasm download, failed compiler fetch, mobile layout and keyboard
execution. It also checks that no source is uploaded and stale compiler assets
are rejected. All three browser engines run this suite in CI; Chromium gates
the Pages deployment too.

`check_library.py` executes generated JavaScript bindings in Node against the
reference and self-hosted compilers, raw and optimized (the `reference` and
`selfhost` suites). The `native` suite also runs it against the standalone CLI.
Set `SNEK_LIBRARY_NATIVE` to a native executable to run the same host tests through
the standalone build command. Coverage includes scalar/string conversion,
initialization, module linking, exception recovery and invalid boundaries. Host
imports also cover missing callbacks, copied strings, scalar return validation,
sync-Promise/reentry rejection, async resolved-value validation, rejection/awaited
cleanup, overlapping-call guards, lazy coroutine startup, and scratch-memory reuse
within long-running calls. Browser checks include a real awaited-fetch example.
`HostObject` cases cover identity, nullability, containers, closures, generators,
retention across awaits, primitive/thenable rejection and opaque-operation
diagnostics. Browser cases pass real DOM elements through raw and optimized
modules; the Vite fixture also checks the generated `object` TypeScript boundary.

Use `python tests/run_suites.py` as the test entry point (default: `fast`). These
tests use `check_*.py`, not unittest's default `test_*.py` discovery convention.
The runner verifies that every check script belongs to a suite, runs each script
in a separate process, and fails if a unittest run discovers zero tests or skips
a case. New check scripts must be registered in `run_suites.py`; an omitted or
deleted file is a preflight error even when running a different suite.

```sh
python tests/run_suites.py all --list  # inventory, routes and prerequisites only
python tests/run_suites.py fast
python tests/run_suites.py reference bootstrap
```

| Suite | Coverage | Preparation |
| --- | --- | --- |
| `browser` | Both compilers' raw/optimized libraries, host interop and workers | Playwright engine, Binaryen, full compiler proof |
| `bundler` | Vite dev/production, TypeScript, asset bases and worker chunks | Browser prerequisites, Node 25.9, `npm ci --prefix examples/vite` |
| `playground` | Browser-based compiler, execution, limits and recovery | Playwright, docs dependencies, verified compiler bootstrap |
| `fast` | Pure compiler/encoding invariants, benchmark/tooling regressions, suite policy | Node 24+, Python Wasmtime, wasm-tools oracle |
| `tooling` | Portable benchmark, profile, lazy runtime and runner regressions | Python |
| `reference` | Full raw/O3 example and negative corpus, byte I/O, formatting, string fusion | Runtime, Binaryen, Python Wasmtime |
| `bootstrap` | Parser, frontend, declarations, encoder fixed point, emitter and machine-pass proofs | Runtime, Binaryen, `bootstrap/bootstrap_checker.py` output for emitter/machine proofs |
| `checkers` | Explicit scalar/aggregate/runtime/ordered seed variants, rebuilt checker and full language cases | Runtime, Binaryen, wasm-tools, Python Wasmtime; fixtures build their own temporary checkers |
| `compiler` | Published full-compiler fixed point and no-host-fallback guards | Fresh `bootstrap/bootstrap_compiler.py` output, runtime |
| `selfhost` | `compiler`, lazy helpers, full raw/O3 corpus with compiler=snek | Full compiler proof, runtime, Binaryen |
| `components` | Reference component corpus, streams, host library and lifecycle | Wasmtime CLI 49+, wasm-tools 1.250+, Binaryen, Python Wasmtime, Node 24+ |
| `selfhost-components` | Full-compiler component corpus, independent stream/host guards, lifecycle | Same component tools, full compiler proof |
| `checker-components` | Historical rebuilt-checker/Python-emitter component variant | Same component tools plus `bootstrap/bootstrap_checker.py` output |
| `native` | Native CLI, embedded compiler fixed point, full language corpus | Release executable and full compiler proof |
| `packaging` | Offline npx/bunx/uvx installation and real execution | Platform packages in `dist/`, npm, Bun, uv |
| `all` | Every registered route, deduplicated | All preparations above |

Each route explicitly selects the Python or self-hosted compiler; ambient
frontend, compiler-image and experimental machine-pass overrides are removed
from child environments. `SNEK_RUNTIME`, `SNEK_NODE`, `SNEK_WASM_OPT`, and
`SNEK_WASM_TOOLS` retain their usual meaning; component routes always select the
Wasmtime CLI. Required tools/artifacts are checked before any scripts run. The
runner does not build prerequisites or accept old compiler proofs as a substitute
for the fixed-point/freshness checks.

Skips are failures by default. `--allow-skips` explicitly relaxes this policy for
local exploration and still prints every skipped case; it does not waive suite
prerequisites or enable a raw-only run of an optimized suite. CI never uses this
blanket option. Windows packaging jobs permit exactly `requires POSIX wait4`
with `--allow-skip-reason`, because Windows cannot implement that process-memory
test. All other skips remain failures. Individual scripts remain directly usable
for targeted development (for example `check_native.py --binary ...`).

The body-checker modules expose case-group tuples and compose each variant with
either a seed or rebuilt-checker fixture. Assertions and fixture setup are reused,
but test classes no longer inherit other test classes. The historical cumulative
variants are retained deliberately: rebuilt variants repeat earlier semantics
against their rebuilt checker rather than merely reusing a seed's result.

`check_modules.py` runs local-module fixtures through the reference and
self-hosted compilers, on core and component routes. It compares supported
`.py` projects against CPython, checks namespace/initialization behavior and
source diagnostics, and rejects ambiguous/escaping paths and source-overwriting
outputs. Native CLI tests additionally exercise imports with an empty `PATH`.

CI runs every registered script on pushes and pull requests. Its two independent
full compiler bootstrap jobs start from a clean checkout with no restored compiler
artifacts (Node and Python Wasmtime). Separate regression jobs cover the reference
compiler and pass-level/historical proofs. The package matrix exercises native and
offline launcher suites for each supported platform; no workflow publishes packages.
