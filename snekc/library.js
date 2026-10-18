export class SnekError extends Error {
  constructor(message) { super(message); this.name = "SnekError"; }
}

export class SnekCancelledError extends Error {
  constructor(reason) { super("Snek call cancelled", {cause: reason}); this.name = "SnekCancelledError"; }
}

export class SnekCompatibilityError extends Error {
  constructor(message) { super(message); this.name = "SnekCompatibilityError"; }
}

export class SnekLoadError extends Error {
  constructor(message, options) { super(message, options); this.name = "SnekLoadError"; }
}

// Executable probes, not user-agent/version guesses. The second uses the modern
// try_table encoding, not the older exception-handling proposal.
export function assertSupported() {
  if (typeof WebAssembly === "undefined" || typeof WebAssembly.validate !== "function")
    throw new SnekCompatibilityError("Snek requires WebAssembly in this environment.");
  const gc = new Uint8Array([0,97,115,109,1,0,0,0,1,6,2,95,0,96,0,0,3,2,1,1,10,8,1,6,0,251,0,0,26,11]);
  const exceptions = new Uint8Array([0,97,115,109,1,0,0,0,1,4,1,96,0,0,3,2,1,0,13,3,1,0,0,10,16,1,14,0,2,64,31,64,1,0,0,0,8,0,11,11,11]);
  if (!WebAssembly.validate(gc))
    throw new SnekCompatibilityError("Snek requires WebAssembly GC. Update your browser or use a supported runtime.");
  if (!WebAssembly.validate(exceptions))
    throw new SnekCompatibilityError("Snek requires WebAssembly exception handling (try_table). Update your browser or use a supported runtime.");
}

// Loading is explicit: pass bytes/a Module in Node, or a URL/Response in a browser.
export async function instantiate(source, { stdout = () => {}, imports: host = {} } = {}) {
  assertSupported();
  if (typeof stdout !== "function") throw new TypeError("stdout must be a function");
  if (host === null || typeof host !== "object") throw new TypeError("imports must be an object");
  // Snapshot explicitly supplied own properties; never bind Object.prototype.
  const callbacks = Object.create(null);
  for (const [name] of hostSignatures) {
    const callback = Object.hasOwn(host, name) ? host[name] : undefined;
    if (typeof callback !== "function")
      throw new TypeError(`Missing host import function: ${name}`);
    callbacks[name] = callback;
  }
  if (typeof source === "string" || source instanceof URL) {
    const url = String(source);
    try { source = await fetch(source); }
    catch (cause) {
      throw new SnekLoadError(`Unable to fetch Snek library ${url}. Check the URL, network, CORS, and Content Security Policy.`, { cause });
    }
  }
  if (typeof Response !== "undefined" && source instanceof Response) {
    if (!source.ok) throw new SnekLoadError(`Unable to load Snek library: HTTP ${source.status}${source.url ? ` (${source.url})` : ""}.`);
    try { source = await source.arrayBuffer(); }
    catch (cause) { throw new SnekLoadError("Unable to read the Snek library response body.", { cause }); }
  }
  let module;
  try { module = source instanceof WebAssembly.Module ? source : await WebAssembly.compile(source); }
  catch (cause) {
    throw new SnekLoadError("Unable to compile Snek library. Check that the URL returns a Wasm binary, not an HTML page, and that CSP permits WebAssembly compilation.", { cause });
  }
  const encoder = new TextEncoder();
  const decoder = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true });
  const outputDecoder = new TextDecoder("utf-8", { ignoreBOM: true });
  const errorDecoder = new TextDecoder("utf-8", { ignoreBOM: true });
  let wasm, busy = false, activeTask = null, errorText = "";
  const callbackStates = callbackSignatures.map(() => ({toWasm: new WeakMap(), toJS: new WeakMap()}));
  const listStates = listSignatures.map(() => ({toWasm: new WeakMap(), toJS: new WeakMap()}));
  const dictStates = dictSignatures.map(() => ({toWasm: new WeakMap(), toJS: new WeakMap()}));
  const tupleStates = tupleSignatures.map(() => ({toWasm: new WeakMap(), toJS: new WeakMap()}));
  // The private field roots the GC object even if the API object is discarded.
  // A fresh private brand per instance rejects foreign handles, and a single
  // identity map preserves wrappers across base/derived boundary types.
  const classHandles = new WeakMap();
  class SnekObjectHandle {
    #value;
    constructor(value) {
      this.#value = value;
      Object.setPrototypeOf(this, null);
      Object.freeze(this);
    }
    static unwrap(value) {
      return value !== null && typeof value === "object" && #value in value ? value.#value : undefined;
    }
  }
  const imports = { wasi_snapshot_preview1: {
    fd_write(fd, iovs, count, written) {
      let total = 0;
      for (let i = 0; i < count; i++) {
        // stdout may use list handles and grow memory. Never retain a view
        // across an application callback, including the final byte count.
        const view = new DataView(wasm.memory.buffer);
        const ptr = view.getUint32(iovs + i * 8, true);
        const len = view.getUint32(iovs + i * 8 + 4, true);
        const bytes = new Uint8Array(wasm.memory.buffer, ptr, len);
        if (fd === 1) stdout(outputDecoder.decode(bytes, { stream: true }));
        else if (fd === 2) errorText += errorDecoder.decode(bytes, { stream: true });
        else return 8; // EBADF
        total += len;
      }
      new DataView(wasm.memory.buffer).setUint32(written, total, true);
      return 0;
    },
    proc_exit(code) { throw new SnekError(errorText.trimEnd() || `Snek exited with status ${code}`); }
  }};
  imports.snek_host = Object.create(null);
  imports.snek_host_async = Object.create(null);
  imports.snek_object = { same: (a, b) => Number(a === b) };
  imports.snek_host_result = Object.create(null);
  function lowerResult(name, result, value) {
    if (value != null && typeof value.then === "function") {
      // A misdeclared async callback must not also leak an unhandled rejection.
      Promise.resolve(value).catch(() => {});
      throw new TypeError(`Host import ${name} must be synchronous; received a Promise/thenable`);
    }
    if (result === "None") {
      if (value !== undefined) throw new TypeError(`Host import ${name} must return undefined`);
      return;
    }
    const lowered = lower(result, value);
    return result === "str" ? lowered : lowered[0];
  }
  imports.snek_async = {
    status(pending) {
      if (!activeTask?.requests.has(pending)) throw new Error("Invalid async host request state");
      if (!pending.settled) return 0;
      if (!pending.failed) {
        try { pending.lowered = lowerResult(pending.name, pending.result, pending.value); }
        catch (error) { pending.failed = true; pending.error = error; }
      }
      return pending.failed ? -1 : 1;
    },
    error(pending) {
      if (!activeTask?.requests.has(pending)) throw new Error("Invalid async host request state");
      let message;
      try { message = String(pending.error instanceof Error ? pending.error.message : pending.error); }
      catch { message = "JavaScript callback failed"; }
      // Even arbitrary rejection values must yield a valid Snek string.
      message = decoder.decode(encoder.encode(`Host import ${pending.name}: ${message}`));
      activeTask.requests.delete(pending);
      return lower("str", message);
    },
    drop(pending) { activeTask?.requests.delete(pending); },
  };
  function liftArguments(params, values) {
    let cursor = 0;
    return params.map(type => {
      const value = values[cursor++];
      if (type === "str") return decoder.decode(new Uint8Array(wasm.memory.buffer, value >>> 0, values[cursor++] >>> 0));
      return lift(type, value);
    });
  }
  imports.snek_callback = Object.create(null);
  imports.snek_callback_async = Object.create(null);
  imports.snek_callback_result = Object.create(null);
  callbackSignatures.forEach(([, params, result, isAsync], index) => {
    if (isAsync) {
      const name = `callback ${index}`;
      imports.snek_callback_async[index] = (callback, ...values) => startAsync(name, params, result, callback, values);
      imports.snek_callback_result[index] = pending => takeAsync(name, pending);
    } else {
      imports.snek_callback[index] = (callback, ...values) =>
        lowerResult("callback", result, Reflect.apply(callback, undefined, liftArguments(params, values)));
    }
  });
  function startAsync(name, params, result, callback, values) {
    if (!activeTask) throw new Error("Async host imports require an async export; use await, not run()");
    const owner = activeTask;
    const state = {name, result, settled: false, failed: false};
    activeTask.requests.add(state);
    try {
      const value = Reflect.apply(callback, undefined, liftArguments(params, values));
      if (value == null || typeof value.then !== "function")
        throw new TypeError(`Async host import ${name} must return a Promise/thenable`);
      Promise.resolve(value).then(
        value => { state.value = value; state.settled = true; owner.wake?.(); },
        error => { state.error = error; state.failed = true; state.settled = true; owner.wake?.(); });
    } catch (error) {
      state.error = error; state.failed = true; state.settled = true;
    }
    return state;
  }
  function takeAsync(name, pending) {
    if (!activeTask?.requests.has(pending) || pending.name !== name || !pending.settled || pending.failed)
      throw new Error("Invalid async host result state");
    const value = pending.lowered;
    activeTask.requests.delete(pending);
    return value;
  }
  for (const [name, params, result, isAsync] of hostSignatures) {
    if (!isAsync) {
      imports.snek_host[name] = (...values) => lowerResult(name, result,
        Reflect.apply(callbacks[name], undefined, liftArguments(params, values)));
      continue;
    }
    imports.snek_host_async[name] = (...values) => startAsync(name, params, result, callbacks[name], values);
    imports.snek_host_result[name] = pending => takeAsync(name, pending);
  }
  let instance;
  try { instance = await WebAssembly.instantiate(module, imports); }
  catch (cause) {
    throw new SnekLoadError("Unable to instantiate Snek library. Use the .wasm and .js files from the same --library build.", { cause });
  }
  wasm = instance.exports;
  if (wasm.__snek_abi?.value !== 7 || !(wasm.__snek_yielded instanceof WebAssembly.Global) || !(wasm.memory instanceof WebAssembly.Memory) || !(wasm.__snek_length instanceof WebAssembly.Global) ||
      ["__snek_init", "__snek_reset", "__snek_alloc", "__snek_mark", "__snek_restore",
        ...listSignatures.flatMap((_, i) => ["len", "get", "set", "append", "pop"].map(op => `__snek_list_${op}_${i}`)),
        ...dictSignatures.flatMap((_, i) => ["len", "next", "get", "contains", "set", "delete", "clear", "copy", "keys_at", "values_at"].map(op => `__snek_dict_${op}_${i}`)),
        ...tupleSignatures.flatMap(([, fields], i) => fields.map((_, field) => `__snek_tuple_get_${i}_${field}`)),
        ...classSignatures.map((_, i) => `__snek_class_test_${i}`),
        ...callbackSignatures.flatMap(([, , , isAsync], i) =>
        [`__snek_wrap_${i}`, `__snek_call_${i}`, ...(isAsync ? [`__snek_step___snek_callback_${i}`, `__snek_result___snek_callback_${i}`] : [])]), ...signatures.flatMap(([name, , , isAsync]) =>
        isAsync ? [name, `__snek_step_${name}`, `__snek_result_${name}`] : [name])]
        .some(name => typeof wasm[name] !== "function"))
    throw new SnekLoadError("Invalid Snek library exports. Use the .wasm and .js files from the same --library build.");
  function begin(task = null) {
    if (busy) throw new Error("Snek library calls are not reentrant");
    busy = true;
    activeTask = task;
    errorText = "";
    wasm.__snek_reset();
  }
  function finish() {
    wasm.__snek_reset();
    busy = false;
    activeTask = null;
    // Reset incremental decoders even when a host callback throws.
    outputDecoder.decode();
    errorDecoder.decode();
  }
  function enter(action) {
    begin();
    try { return action(); }
    finally { finish(); }
  }
  // These operations cannot call user Snek code. A host callback may use its
  // collection arguments while a Wasm step is active, without resetting that step's
  // scratch strings, output, yielded flag, or pending request ownership.
  function collectionOperation(action) {
    if (!busy) return enter(action);
    const mark = wasm.__snek_mark(), length = wasm.__snek_length.value;
    try { return action(); }
    finally { wasm.__snek_restore(mark); wasm.__snek_length.value = length; }
  }
  // Only synchronous execution owns the instance. Each suspended invocation
  // retains its own GC frame and pending host result, never borrowed memory.
  function enterTask(task, action) {
    begin(task);
    try { return action(); }
    finally {
      task.runnable = Boolean(wasm.__snek_yielded.value);
      finish();
    }
  }
  function lower(type, value) {
    const object = classSignatures.findIndex(([signature]) => signature === type);
    if (object !== -1) {
      const [, name, nullable] = classSignatures[object];
      if (value === null && nullable) return [null];
      const raw = SnekObjectHandle.unwrap(value);
      if (!raw || !wasm[`__snek_class_test_${object}`](raw))
        throw new TypeError(`expected ${name} handle from this Snek instance${nullable ? " or null" : ""}`);
      return [raw];
    }
    const list = listSignatures.findIndex(([signature]) => signature === type);
    if (list !== -1) {
      const raw = listStates[list].toWasm.get(value);
      if (!raw) throw new TypeError(`expected ${type} handle from this Snek instance`);
      return [raw];
    }
    const dict = dictSignatures.findIndex(([signature]) => signature === type);
    if (dict !== -1) {
      const raw = dictStates[dict].toWasm.get(value);
      if (!raw) throw new TypeError(`expected ${type} handle from this Snek instance`);
      return [raw];
    }
    const tuple = tupleSignatures.findIndex(([signature]) => signature === type);
    if (tuple !== -1) {
      const raw = tupleStates[tuple].toWasm.get(value);
      if (!raw) throw new TypeError(`expected ${type} handle from this Snek instance`);
      return [raw];
    }
    const callback = callbackSignatures.findIndex(([signature]) => signature === type);
    if (callback !== -1) {
      if (typeof value !== "function") throw new TypeError("expected function for " + type);
      const state = callbackStates[callback];
      let wrapped = state.toWasm.get(value);
      if (!wrapped) {
        wrapped = wasm[`__snek_wrap_${callback}`](value);
        state.toWasm.set(value, wrapped);
        state.toJS.set(wrapped, value);
      }
      return [wrapped];
    }
    if (type === "HostObject" || type === "HostObject | None") {
      if (value === null && type === "HostObject | None") return [null];
      if (value === null || (typeof value !== "object" && typeof value !== "function"))
        throw new TypeError("expected HostObject (a JavaScript object)");
      if (typeof value.then === "function")
        throw new TypeError("HostObject cannot be a Promise/thenable");
      return [value];
    }
    if (type === "str") {
      if (typeof value !== "string") throw new TypeError("expected string");
      // Reject lone UTF-16 surrogates instead of silently replacing them.
      for (let i = 0; i < value.length; i++) {
        const c = value.charCodeAt(i);
        if (c >= 0xd800 && c <= 0xdbff) {
          const next = value.charCodeAt(++i);
          if (!(next >= 0xdc00 && next <= 0xdfff)) throw new TypeError("string contains an unpaired surrogate");
        } else if (c >= 0xdc00 && c <= 0xdfff) throw new TypeError("string contains an unpaired surrogate");
      }
      const bytes = encoder.encode(value);
      const ptr = wasm.__snek_alloc(bytes.length) >>> 0;
      new Uint8Array(wasm.memory.buffer, ptr, bytes.length).set(bytes);
      return [ptr, bytes.length];
    }
    if (type === "bool") {
      if (typeof value !== "boolean") throw new TypeError("expected boolean");
      return [Number(value)];
    }
    if (type === "i64") {
      if (typeof value !== "bigint") throw new TypeError("expected bigint for i64");
      if (value < -(1n << 63n) || value >= 1n << 63n) throw new RangeError("i64 out of range");
    } else {
      if (typeof value !== "number") throw new TypeError("expected number");
      if (type === "i32" && (!Number.isInteger(value) || value < -2147483648 || value > 2147483647))
        throw new RangeError("i32 out of range");
    }
    return [value];
  }
  enter(() => wasm.__snek_init());
  const api = Object.create(null);
  function lift(result, value) {
    const object = classSignatures.findIndex(([signature]) => signature === result);
    if (object !== -1) {
      if (value === null) return null;
      let wrapped = classHandles.get(value);
      if (!wrapped) {
        wrapped = new SnekObjectHandle(value);
        classHandles.set(value, wrapped);
      }
      return wrapped;
    }
    const list = listSignatures.findIndex(([signature]) => signature === result);
    if (list !== -1) {
      const state = listStates[list];
      let wrapped = state.toJS.get(value);
      if (!wrapped) {
        const [, element] = listSignatures[list];
        const size = () => wasm[`__snek_list_len_${list}`](value);
        const index = i => {
          if (!Number.isSafeInteger(i)) throw new TypeError("list index must be a safe integer");
          const length = size();
          if (i < 0) i += length;
          if (i < 0 || i >= length) throw new RangeError("list index out of range");
          return i;
        };
        wrapped = Object.freeze({
          get length() { return collectionOperation(size); },
          get(i) { return collectionOperation(() => lift(element, wasm[`__snek_list_get_${list}`](value, index(i)))); },
          set(i, item) { return collectionOperation(() => {
            const lowered = lower(element, item);
            wasm[`__snek_list_set_${list}`](value, index(i), ...lowered);
          }); },
          append(item) { return collectionOperation(() => wasm[`__snek_list_append_${list}`](value, ...lower(element, item))); },
          pop() { return collectionOperation(() => {
            if (size() === 0) throw new RangeError("pop from empty list");
            return lift(element, wasm[`__snek_list_pop_${list}`](value));
          }); },
          toArray() {
            const items = [];
            for (let i = 0, length = size(); i < length; i++) items.push(wrapped.get(i));
            return items;
          },
        });
        state.toJS.set(value, wrapped);
        state.toWasm.set(wrapped, value);
      }
      return wrapped;
    }
    const dict = dictSignatures.findIndex(([signature]) => signature === result);
    if (dict !== -1) {
      const state = dictStates[dict];
      let wrapped = state.toJS.get(value);
      if (!wrapped) {
        const [, keyType, valueType] = dictSignatures[dict];
        const call = (op, ...args) => wasm[`__snek_dict_${op}_${dict}`](value, ...args);
        wrapped = Object.freeze({
          get size() { return collectionOperation(() => call("len")); },
          get(key) { return collectionOperation(() => {
            const args = lower(keyType, key);
            return call("contains", ...args) ? lift(valueType, call("get", ...args)) : undefined;
          }); },
          has(key) { return collectionOperation(() => Boolean(call("contains", ...lower(keyType, key)))); },
          set(key, item) { return collectionOperation(() => call("set", ...lower(keyType, key), ...lower(valueType, item))); },
          delete(key) { return collectionOperation(() => {
            const args = lower(keyType, key);
            if (!call("contains", ...args)) return false;
            call("delete", ...args);
            return true;
          }); },
          clear() { return collectionOperation(() => call("clear")); },
          entries() { return collectionOperation(() => {
            // Copy entries before lifting values: a HostObject's then getter
            // may run JS that mutates the original dictionary.
            const snapshot = call("copy"), items = [];
            for (let i = wasm[`__snek_dict_next_${dict}`](snapshot, 0); i !== -1;
                 i = wasm[`__snek_dict_next_${dict}`](snapshot, i + 1)) {
              const key = lift(keyType, wasm[`__snek_dict_keys_at_${dict}`](snapshot, i));
              items.push([key, lift(valueType, wasm[`__snek_dict_values_at_${dict}`](snapshot, i))]);
            }
            return items;
          }); },
        });
        state.toJS.set(value, wrapped);
        state.toWasm.set(wrapped, value);
      }
      return wrapped;
    }
    const tuple = tupleSignatures.findIndex(([signature]) => signature === result);
    if (tuple !== -1) {
      const state = tupleStates[tuple];
      let wrapped = state.toJS.get(value);
      if (!wrapped) {
        const [, fields] = tupleSignatures[tuple];
        wrapped = Object.freeze({
          length: fields.length,
          get(index) { return collectionOperation(() => {
            if (!Number.isSafeInteger(index)) throw new TypeError("tuple index must be a safe integer");
            if (index < 0 || index >= fields.length) throw new RangeError("tuple index out of range");
            return lift(fields[index], wasm[`__snek_tuple_get_${tuple}_${index}`](value));
          }); },
          toArray() { return Object.freeze(fields.map((_, index) => wrapped.get(index))); },
        });
        state.toJS.set(value, wrapped);
        state.toWasm.set(wrapped, value);
      }
      return wrapped;
    }
    const callback = callbackSignatures.findIndex(([signature]) => signature === result);
    if (callback !== -1) {
      const state = callbackStates[callback];
      let wrapped = state.toJS.get(value);
      if (!wrapped) {
        const [, params, ret, isAsync] = callbackSignatures[callback];
        const call = args => {
          if (args.length !== params.length) throw new TypeError(`callback expects ${params.length} arguments`);
          const lowered = params.flatMap((type, i) => lower(type, args[i]));
          return wasm[`__snek_call_${callback}`](value, ...lowered);
        };
        wrapped = isAsync ? (...args) => runAsync(`__snek_callback_${callback}`, ret, () => call(args)) :
          (...args) => enter(() => lift(ret, call(args)));
        state.toJS.set(value, wrapped);
        state.toWasm.set(wrapped, value);
      }
      return wrapped;
    }
    if (result === "HostObject" || result === "HostObject | None") return lower(result, value)[0];
    if (result === "str") return decoder.decode(new Uint8Array(wasm.memory.buffer, value >>> 0, wasm.__snek_length.value >>> 0));
    return result === "bool" ? Boolean(value) : value;
  }
  async function runAsync(name, result, create, options = () => undefined) {
    const signal = options();
    if (signal?.aborted) throw new SnekCancelledError(signal.reason);
    const task = {requests: new Set(), cancelled: false, injected: false, wake: null};
    const abort = () => {task.cancelled = true; if (!task.injected) task.wake?.();};
    let frame;
    try {
      signal?.addEventListener("abort", abort, {once: true});
      frame = enterTask(task, create);
      while (true) {
        const mode = task.cancelled ? (task.injected ? 2 : 1) : 0;
        if (mode === 1) task.injected = true;
        if (!enterTask(task, () => wasm[`__snek_step_${name}`](frame, mode))) break;
        if (task.cancelled && !task.injected) continue;
        if (task.requests.size && !task.runnable) {
          // One waiter per invocation, not repeated Promise.race handlers on a
          // long-lived request while other children keep making progress.
          await new Promise(resolve => {
            task.wake = resolve;
            if ([...task.requests].some(request => request.settled) || (task.cancelled && !task.injected)) resolve();
          });
          task.wake = null;
        } else await new Promise(resolve => setTimeout(resolve, 0));
      }
      if (task.cancelled) throw new SnekCancelledError(signal?.reason);
      return enterTask(task, () => lift(result, wasm[`__snek_result_${name}`](frame)));
    } finally {
      frame = null;
      task.wake = null;
      task.requests.clear();
      signal?.removeEventListener("abort", abort);
    }
  }
  for (const [name, params, result, isAsync] of signatures) {
    const argumentsToWasm = args => {
      if (args.length !== params.length) throw new TypeError(`${name} expects ${params.length} arguments`);
      return params.flatMap((type, i) => lower(type, args[i]));
    };
    if (!isAsync) {
      api[name] = (...args) => enter(() => lift(result, wasm[name](...argumentsToWasm(args))));
      continue;
    }
    api[name] = (...args) => runAsync(name, result,
      () => wasm[name](...argumentsToWasm(args.slice(0, params.length))), () => {
        if (args.length !== params.length && args.length !== params.length + 1)
          throw new TypeError(`${name} expects ${params.length} arguments and optional call options`);
        const options = args[params.length];
        if (options !== undefined && (options === null || typeof options !== "object" || Array.isArray(options)))
          throw new TypeError("call options must be an object");
        const signal = options?.signal;
        if (signal !== undefined && (signal === null || typeof signal.aborted !== "boolean" ||
            typeof signal.addEventListener !== "function" || typeof signal.removeEventListener !== "function"))
          throw new TypeError("signal must be an AbortSignal");
        return signal;
      });
  }
  return Object.freeze(api);
}
