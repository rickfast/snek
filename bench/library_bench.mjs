// Warm-instance boundary cost, including JS validation and handle/root management.
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {pathToFileURL} from 'node:url';

const spec = JSON.parse(await readFile(process.argv[2], 'utf8'));
const {instantiate} = await import(pathToFileURL(spec.bindings));
const lib = await instantiate(await readFile(spec.wasm), {imports: {increment: async x => x + 1}});
const list = lib.values(), dict = lib.mapping();
const n = spec.iterations;
assert.ok(Number.isSafeInteger(n) && n > 0 && n <= 1000000);
assert.ok(Number.isSafeInteger(spec.reps) && spec.reps > 0);
assert.ok(Number.isSafeInteger(spec.warmups) && spec.warmups >= 0);
const increment = x => x + 1;
const short = 'hello', unicode = '🐍é中'.repeat(128);
const cases = [
  ['js_scalar_control', () => increment(41), 42, 'call'],
  ['scalar_call', () => lib.scalar(41), 42, 'call'],
  ['string_5_bytes', () => lib.text(short).length, short.length, 'call'],
  ['string_1152_bytes', () => lib.text(unicode).length, unicode.length, 'call'],
  ['list_handle_roundtrip', () => +(lib.echo_list(list) === list), 1, 'call'],
  ['dict_handle_roundtrip', () => +(lib.echo_dict(dict) === dict), 1, 'call'],
  ['list_get', () => list.get(0), 7, 'call'],
  ['dict_get', () => dict.get('value'), 7, 'call'],
  ['callback_roundtrip', () => lib.callback(increment, 41), 42, 'call'],
  ['generator_next', null, null, 'yield'],
  ['async_host_roundtrip', null, 42, 'awaited call'],
];
const results = Object.fromEntries(cases.map(([name, , , unit]) => [name, {samples_ns: [], unit}]));
for (let round = -spec.warmups; round < spec.reps; round++) {
  // Rotate order to reduce systematic warming/order bias. Keep every timed sample.
  for (let offset = 0; offset < cases.length; offset++) {
    const [name, fn, expected] = cases[(round + spec.warmups + offset) % cases.length];
    let checksum = 0;
    const iterator = name === 'generator_next' ? lib.numbers(n) : null;
    const start = performance.now();
    if (iterator) {
      for (let i = 0; i < n; i++) {
        const value = iterator.next();
        if (value.done) throw new Error('generator ended early');
        checksum += value.value;
      }
    } else if (name === 'async_host_roundtrip') {
      for (let i = 0; i < n; i++) checksum += await lib.async_host(41);
    } else {
      for (let i = 0; i < n; i++) checksum += fn();
    }
    const elapsed = (performance.now() - start) * 1e6 / n;
    assert.equal(checksum, iterator ? n * (n - 1) / 2 : expected * n, name);
    if (iterator) assert.equal(iterator.next().done, true);
    assert.ok(Number.isFinite(elapsed) && elapsed >= 0);
    if (round >= 0) results[name].samples_ns.push(elapsed);
  }
}
console.log(JSON.stringify(results));
