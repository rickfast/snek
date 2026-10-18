import { assertSupported } from "./generated/bindings.js";
import { runCommand } from "./wasi.js";

const encoder = new TextEncoder(), decoder = new TextDecoder();
self.onmessage = async ({ data }) => {
  try {
    assertSupported();
    const source = encoder.encode(data.source), stdin = encoder.encode(data.stdin);
    if (source.length > 65536 || stdin.length > 65536) throw new Error("Source and input must each fit in 64 KiB.");
    self.postMessage({ type: "phase", phase: "loading", message: "Loading the self-hosted compiler…" });
    const response = await fetch(new URL("./generated/compiler.wasm", import.meta.url));
    if (!response.ok) throw new Error(`Unable to load the compiler: HTTP ${response.status}. Try reloading the page.`);
    const compiler = await WebAssembly.compile(await response.arrayBuffer());
    self.postMessage({ type: "phase", phase: "compiling", message: "Compiling in Snek…" });
    const start = performance.now();
    const compiled = await runCommand(compiler, source, { args: ["snekc"], limit: 16777216 });
    if (compiled.code || compiled.stderr.length) {
      self.postMessage({ type: "error", message: decoder.decode(compiled.stderr) || `Compiler exited with code ${compiled.code}.` });
      return;
    }
    const program = await WebAssembly.compile(compiled.stdout);
    self.postMessage({ type: "compiled", binary: compiled.stdout, milliseconds: performance.now() - start });
    self.postMessage({ type: "phase", phase: "running", message: "Running in a worker…" });
    const executed = await runCommand(program, stdin);
    self.postMessage({ type: "result", code: executed.code,
      stdout: decoder.decode(executed.stdout), stderr: decoder.decode(executed.stderr) });
  } catch (error) {
    self.postMessage({ type: "error", message: `${error.name}: ${error.message}` });
  }
};
