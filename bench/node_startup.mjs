// node node_startup.mjs <snek-v8|pyodide|micropython> <empty.snek.wasm>
// Prints {compile, start} in seconds. For Pyodide and MicroPython the loader compiles and
// initializes in one step, so everything is reported as start.
import { readFile } from 'node:fs/promises';
import { WASI } from 'node:wasi';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

const [kind, emptyWasm] = process.argv.slice(2);
const RT = process.env.BENCH_RT;
let compile = 0, start = 0;
if (kind === 'snek-v8') {
  const bytes = await readFile(emptyWasm);
  const t0 = performance.now();
  const mod = await WebAssembly.compile(bytes);
  const t1 = performance.now();
  const wasi = new WASI({ version: 'preview1', args: ['empty'], env: {}, returnOnExit: true });
  const inst = await WebAssembly.instantiate(mod, wasi.getImportObject());
  wasi.start(inst);
  const t2 = performance.now();
  compile = (t1 - t0) / 1000; start = (t2 - t1) / 1000;
} else if (kind === 'pyodide') {
  const { loadPyodide } = await import(pathToFileURL(join(RT, 'pyodide/pyodide.mjs')).href);
  const t0 = performance.now();
  const py = await loadPyodide();
  py.runPython('pass');
  start = (performance.now() - t0) / 1000;
} else {
  const { loadMicroPython } = await import(pathToFileURL(join(RT, '@micropython/micropython-webassembly-pyscript/micropython.mjs')).href);
  const t0 = performance.now();
  const mp = await loadMicroPython({ stdout: () => {} });
  mp.runPython('pass');
  start = (performance.now() - t0) / 1000;
}
console.log(JSON.stringify({ compile, start }));
