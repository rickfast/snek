# Browser support

Snek's `--library` target runs typed functions in browsers without a WASI runtime.
Build once and serve the generated `.js` and `.wasm` files over HTTP. See
[JavaScript libraries](libraries.md) for the ABI, supported types, and build commands.
Browser APIs stay in JavaScript. Snek can call application-supplied
functions through [typed host imports](libraries.md#calling-javascript-from-snek),
including DOM updates and reads. [Async host imports](libraries.md#async-imports-and-exports)
let Snek await JavaScript Promises from APIs such as `fetch` and timers.
[Opaque `HostObject` references](libraries.md#opaque-javascript-objects) let Snek
retain and pass DOM nodes and other JavaScript objects without accessing their properties.

## Compatibility and loading

The loader checks executable Wasm probes for GC and modern exception handling
(`try_table`) before fetching a program. You can check explicitly when deciding
whether to enable a feature in your UI:

```javascript
import { assertSupported, instantiate, SnekCompatibilityError } from "./greetings.js";

try {
  assertSupported();
  const lib = await instantiate(new URL("./greetings.wasm", import.meta.url));
  document.querySelector("#result").textContent = lib.greet("browser");
} catch (error) {
  document.querySelector("#result").textContent = error.message;
}
```

Errors distinguish three situations:

| Error | Meaning |
| --- | --- |
| `SnekCompatibilityError` | The environment lacks WebAssembly, WasmGC, or modern exception handling |
| `SnekLoadError` | Fetching, reading, compiling, or linking the library failed; the original error is available as `cause` when applicable |
| `SnekError` | A Snek exception escaped initialization or an exported function |

Serve the actual binary, not an HTML fallback page from a router. Cross-origin
URLs need appropriate CORS headers. Your Content Security Policy must allow
fetching the binary and compiling WebAssembly; the loader never changes policy or
uses an alternate execution path to bypass it. A `Response` with a generic MIME
type also works because the loader compiles its bytes rather than requiring
streaming compilation.

Use `.js` and `.wasm` artifacts from the same build. The loader checks required
exports, but this is not a cryptographic check that two artifacts belong together.

## Web Workers

Async exports yield while awaiting host Promises, but computation between awaits
still runs on the calling thread. For expensive computation, instantiate the
library in a module worker to keep the page responsive. The checked-in
`examples/library/worker.mjs` lazily creates one instance and accepts calls of the
form `{id, method, args}`:

```javascript
const worker = new Worker(new URL("./worker.mjs", import.meta.url), { type: "module" });
worker.onmessage = ({ data }) => {
  console.log(data.id, data.error ?? data.value);
};
worker.onerror = error => console.error(error.message);
worker.postMessage({ id: 1, method: "greet", args: ["worker 🐍"] });
worker.postMessage({ id: 2, method: "twice", args: [9007199254740993n] });
// Call worker.terminate() when the owning UI is disposed.
```

Results and errors are returned with their request ID. Integers use `bigint`
through structured cloning, not JSON. There is no shared memory, and this example
does not require cross-origin isolation. The worker example is deliberately small:
request timeouts, cancellation, and a typed RPC client are application concerns.

## Tests and roadmap

The `Browser libraries` CI workflow runs the pinned Playwright Chromium, Firefox,
and WebKit engines. Each must execute both compilers' raw and optimized output;
unsupported engines fail the tests rather than being silently skipped. This is
engine coverage, not a claim that every released browser version works.

To run one engine locally:

```sh
python -m pip install -r tests/requirements-browser.txt
python -m playwright install chromium
python bootstrap/bootstrap_compiler.py
SNEK_BROWSER=chromium python tests/run_suites.py browser
```

The suite also requires `wasm-opt` and a runtime for the self-hosted compiler.
Repeat with `firefox` or `webkit` after installing that engine. Tests cover loading,
Unicode, large strings, numeric boundaries, exceptions, worker calls, and simulated
unsupported environments. Browser downloads and system dependencies follow the
[Playwright installation instructions](https://playwright.dev/python/docs/browsers).

## Bundlers: Vite and TypeScript

The checked-in `examples/vite/` project bundles Snek bindings, an async fetch
callback, and a module worker. It needs no Wasm bundler plugin. Compile Snek first,
then let Vite process the generated JavaScript and binary as separate assets.
From the repository root, using the development compiler:

```sh
mkdir -p examples/vite/src/generated
python -m snekc build examples/vite/program.py --no-opt --library --export greet --export twice --export load --export identity --export apply --export make_greeter --export notify -o examples/vite/src/generated/program.wasm
cd examples/vite
npm ci
npm run dev
```

With the standalone CLI, replace `python -m snekc` with `snek` and omit `--no-opt`.
The example's locked dependencies are tested with Node 25.9. Re-run the Snek build
when changing `program.py`; Vite does not compile Python or watch its imports.
Generated files and build output are ignored by Git. Keep the `.js`, `.d.ts`, and
`.wasm` files from the same build together in `src/generated/`.

Import the bindings normally and the binary as a URL:

```typescript
import { instantiate } from "./generated/program.js";
import wasmUrl from "./generated/program.wasm?url&no-inline";

const library = await instantiate(wasmUrl, {
  imports: {
    fetch_text: async url => {
      const response = await fetch(url);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return response.text();
    },
  },
});
```

Vite's [asset URL imports](https://vite.dev/guide/assets.html) rewrite filenames
and deployment paths. `no-inline` keeps the binary as a separately fetched file.
Use Snek's `instantiate`, not Vite's raw Wasm initialization: Snek needs its own
string conversion, host imports, exception handling, and async driver.

The example enables `vite/client` types and runs `tsc --noEmit` before bundling.
Generated declarations check required host functions, Promise results and
`i64`/`bigint` arguments. Vite's development server alone does not type-check.
Its worker uses the static `new Worker(new URL("./worker.ts", import.meta.url),
{ type: "module" })` pattern, so Vite emits and rewrites the worker chunk too.

To build for a subdirectory such as GitHub Pages:

```sh
npm run build -- --base=/snek-demo/
npm run preview -- --base=/snek-demo/
```

Deploy `dist/` under that path. `--base=./` is also tested for relative asset URLs.
This is a client-side integration, not SSR support. No bundler can polyfill
WasmGC or modern exception handling; the generated capability checks still apply.

The `bundler` test suite runs development and production builds in each browser
engine, with both compilers and raw/optimized Wasm. It checks async fetch, repeated
worker calls, exact integers, Unicode, TypeScript errors, and hashed asset URLs.
After the browser prerequisites above, run from the repository root:

```sh
npm ci --prefix examples/vite
SNEK_BROWSER=chromium python tests/run_suites.py bundler
```

## Remaining browser work

The first-class browser milestone is complete:

- Implemented: capability checks, loading diagnostics, engine CI, worker example,
  typed host imports with a DOM example, Promise-based async interop with a fetch example,
  tested Vite/TypeScript bundling, and an [interactive documentation playground](../learn/playground.md)
  that compiles and runs programs locally in a worker.
- Implemented separately: opaque, GC-managed `HostObject` references.
- Typed synchronous callbacks cross the boundary in either direction, including closures and deferred event handlers. See [typed callbacks](libraries.md#typed-callbacks).
- Later: task concurrency.
