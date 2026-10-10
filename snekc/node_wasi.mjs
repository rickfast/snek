// Fallback runner: node --no-warnings node_wasi.mjs prog.wasm
// Needs Node 24+: WasmGC and exnref exception handling (Node 22 mis-executes value-producing try_table).
import { readFile } from 'node:fs/promises';
import { WASI } from 'node:wasi';

const [file, ...args] = process.argv.slice(2);
const wasi = new WASI({ version: 'preview1', args: [file, ...args], env: {}, returnOnExit: true });
const module = await WebAssembly.compile(await readFile(file));
const instance = await WebAssembly.instantiate(module, wasi.getImportObject());
process.exitCode = wasi.start(instance);
