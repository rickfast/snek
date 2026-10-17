# Test suites

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

CI runs every registered script on pushes and pull requests. Its two independent
full compiler bootstrap jobs start from a clean checkout with no restored compiler
artifacts (Node and Python Wasmtime). Separate regression jobs cover the reference
compiler and pass-level/historical proofs. The package matrix exercises native and
offline launcher suites for each supported platform; no workflow publishes packages.
