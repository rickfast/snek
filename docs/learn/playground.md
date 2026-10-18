# Try Snek

Edit a program and run it here. The **self-hosted Snek compiler** runs in your
browser, then executes the WebAssembly it produced. Source and input stay on
your machine; nothing is submitted to a compilation service.

<div id="snek-playground" class="snek-playground" data-state="loading">
  <div class="snek-playground-toolbar">
    <label for="snek-example">Example</label>
    <select id="snek-example">
      <option value="hello">Hello, WebAssembly</option>
      <option value="word-count">Unicode word counter</option>
      <option value="streams">Async stdin echo</option>
    </select>
  </div>
  <label for="snek-source">Source · playground.snek</label>
  <textarea id="snek-source" spellcheck="false" autocapitalize="off" autocomplete="off" rows="18" aria-describedby="snek-editor-help"></textarea>
  <p id="snek-editor-help">Ctrl+Enter / ⌘+Enter runs the program. Tab moves to the next control.</p>
  <details>
    <summary>Standard input</summary>
    <label for="snek-input">Text supplied on stdin (UTF-8)</label>
    <textarea id="snek-input" spellcheck="false" rows="3"></textarea>
  </details>
  <div class="snek-playground-toolbar">
    <button id="snek-run" class="md-button md-button--primary" disabled>Run</button>
    <button id="snek-stop" class="md-button" disabled>Stop</button>
    <button id="snek-download" class="md-button" disabled>Download Wasm</button>
  </div>
  <p id="snek-status" role="status" aria-live="polite">Loading the editor…</p>
  <p id="snek-details"></p>
  <label for="snek-output">Output and diagnostics</label>
  <pre id="snek-output" tabindex="0" aria-label="Output and diagnostics"></pre>
</div>
<noscript>This playground needs JavaScript. You can also run these examples with the Snek CLI.</noscript>
<script type="module" src="../../assets/playground/main.js"></script>

## What runs here

The first run downloads the compiler (about 2 MB). Each run starts a fresh worker
and instance. Strings, classes, collections, generators, closures, exceptions,
and core async programs work just as in the CLI. Try the examples above or paste
a single-file program. `print()`, stdin and `sys.argv` are provided; the only
argument is `playground.snek`.

This playground builds unoptimized **command modules**, not JavaScript libraries.
For DOM or fetch callbacks, use the [browser library examples](../reference/browsers.md).
Local modules, filesystem/network access, clocks, randomness, and environment
variables are not provided here. Unsupported host imports produce an error.
Use the [CLI](../reference/cli.md) for those programs and optimized builds.

Source and stdin are limited to 64 KiB each; output is limited to 1 MiB.
Compilation has a 15-second deadline and execution a 3-second deadline. Stop
terminates the worker, including a busy loop; it does not run program cleanup.
Workers keep computation off the UI thread, but are not a hard memory quota.
Avoid running untrusted or allocation-heavy programs. Browser memory limits
still apply. Refreshing the page discards your edits.

The browser must support WasmGC and modern WebAssembly exception handling.
If the compatibility check fails, update your browser or use a
[supported local runtime](../reference/compatibility.md).
