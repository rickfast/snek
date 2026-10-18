// A deliberately small, memory-only command host. No filesystem or network.
const encoder = new TextEncoder();

export async function runCommand(module, input, { args = ["playground.snek"], limit = 1048576 } = {}) {
  let memory, cursor = 0, size = 0;
  const stdout = [], stderr = [];
  const argv = args.map(value => encoder.encode(value + "\0"));
  const view = () => new DataView(memory.buffer);
  const bytes = () => new Uint8Array(memory.buffer);
  const exit = {};
  let code = 0;
  const host = {
    fd_read(fd, iovs, count, read) {
      if (fd !== 0) return 8; // BADF
      const data = view();
      let total = 0;
      for (let i = 0; i < count; i++) {
        const ptr = data.getUint32(iovs + i * 8, true);
        const length = data.getUint32(iovs + i * 8 + 4, true);
        const chunk = input.subarray(cursor, cursor + length);
        bytes().set(chunk, ptr);
        cursor += chunk.length;
        total += chunk.length;
      }
      data.setUint32(read, total, true);
      return 0;
    },
    fd_write(fd, iovs, count, written) {
      if (fd !== 1 && fd !== 2) return 8;
      const data = view();
      let total = 0;
      for (let i = 0; i < count; i++) {
        const ptr = data.getUint32(iovs + i * 8, true);
        const length = data.getUint32(iovs + i * 8 + 4, true);
        if (size + length > limit) throw new Error("Output limit exceeded; reduce the program's output.");
        const chunk = bytes().slice(ptr, ptr + length);
        (fd === 1 ? stdout : stderr).push(chunk);
        size += length;
        total += length;
      }
      data.setUint32(written, total, true);
      return 0;
    },
    args_sizes_get(count, size) {
      view().setUint32(count, argv.length, true);
      view().setUint32(size, argv.reduce((n, value) => n + value.length, 0), true);
      return 0;
    },
    args_get(pointers, buffer) {
      for (let i = 0; i < argv.length; i++) {
        view().setUint32(pointers + i * 4, buffer, true);
        bytes().set(argv[i], buffer);
        buffer += argv[i].length;
      }
      return 0;
    },
    proc_exit(status) { code = status; throw exit; },
  };
  for (const item of WebAssembly.Module.imports(module)) {
    if (item.kind !== "function" || item.module !== "wasi_snapshot_preview1" || !Object.hasOwn(host, item.name))
      throw new Error(`The playground does not provide ${item.module}.${item.name}. Use the CLI for host services.`);
  }
  const instance = await WebAssembly.instantiate(module, { wasi_snapshot_preview1: host });
  memory = instance.exports.memory;
  if (!(memory instanceof WebAssembly.Memory) || typeof instance.exports._start !== "function")
    throw new Error("Expected a Snek command with memory and _start exports.");
  try { instance.exports._start(); }
  catch (error) { if (error !== exit) throw error; }
  const join = chunks => {
    const result = new Uint8Array(chunks.reduce((n, chunk) => n + chunk.length, 0));
    let offset = 0;
    for (const chunk of chunks) { result.set(chunk, offset); offset += chunk.length; }
    return result;
  };
  return { code, stdout: join(stdout), stderr: join(stderr) };
}
