// Runs benchmarks inside Node 24 for: snek-v8 (snek's .wasm on V8), pyodide, micropython.
// Prints one JSON line: {results: {name: {ok, out, times}}, load, compile}
import { readFile } from 'node:fs/promises';
import { WASI } from 'node:wasi';
import { openSync, readFileSync, closeSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
const RT = process.env.BENCH_RT;  // .../rt/node_modules

const spec = JSON.parse(await readFile(process.argv[2], 'utf8'));
const results = {};
let load = null, compile = null;

function splitTime(out) {
  const m = out.match(/^@@TIME (\S+)$/m);
  const t = m ? Number(m[1]) : null;
  return [out.replace(/^@@TIME .*\n?/gm, ''), t !== null && Number.isFinite(t) && t >= 0 ? t : null];
}

if (spec.kind === 'snek-v8') {
  for (const job of spec.jobs) {
    const bytes = await readFile(job.wasm);
    const t0 = performance.now();
    const mod = await WebAssembly.compile(bytes);
    const c = (performance.now() - t0) / 1000;
    if (job.name === 'fib') compile = c;
    const times = []; let out = '', ok = true, err = '';
    for (let i = 0; i < spec.reps; i++) {
      const dir = mkdtempSync(join(tmpdir(), 'snek-'));
      const path = join(dir, 'out');
      const fd = openSync(path, 'w');
      try {
        const wasi = new WASI({ version: 'preview1', args: [job.name], env: {}, stdout: fd, returnOnExit: true });
        const t1 = performance.now();
        const inst = await WebAssembly.instantiate(mod, wasi.getImportObject());
        const code = wasi.start(inst);
        const t2 = performance.now();
        out = readFileSync(path, 'utf8');
        ok = code === 0 && out === job.expected;
        if (!ok) break;
        times.push((t2 - t1) / 1000);
      } catch (e) {
        ok = false; err = String(e); break;
      } finally {
        closeSync(fd);
        rmSync(dir, { recursive: true, force: true });
      }
    }
    results[job.name] = { ok, out, times, err };
  }
} else {
  let run;
  const t0 = performance.now();
  if (spec.kind === 'pyodide') {
    const { loadPyodide } = await import(pathToFileURL(join(RT, 'pyodide/pyodide.mjs')).href);
    const py = await loadPyodide();
    let buf = '';
    py.setStdout({ batched: (s) => { buf += s + '\n'; } });
    run = (src) => { buf = ''; py.runPython(src); return buf; };
  } else {
    const { loadMicroPython } = await import(pathToFileURL(join(RT, '@micropython/micropython-webassembly-pyscript/micropython.mjs')).href);
    let buf = '';
    const mp = await loadMicroPython({ heapsize: 512 * 1024 * 1024, stdout: (s) => { buf += s + '\n'; } });
    run = (src) => { buf = ''; mp.runPython(src); return buf; };
  }
  load = (performance.now() - t0) / 1000;
  for (const job of spec.jobs) {
    const times = []; let out = '', ok = true, err = '';
    for (let i = 0; i < spec.reps; i++) {
      try {
        const [body, t] = splitTime(run(job.source));
        out = body;
        if (t === null || out !== job.expected) { ok = false; break; }
        times.push(t);
        if (job.name !== 'empty' && t > 30) break;
      } catch (e) { ok = false; err = String(e).slice(-800); break; }
    }
    results[job.name] = { ok, out, times, err };
  }
}
console.log(JSON.stringify({ results, load, compile }));
