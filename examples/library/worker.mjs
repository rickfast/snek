// No shared memory or cross-origin isolation required. Each worker owns an instance.
import { instantiate } from "./greetings.js";

let library;
self.onmessage = async ({ data }) => {
  const id = data?.id;
  try {
    const { method, args } = data;
    library ??= instantiate(new URL("./greetings.wasm", import.meta.url));
    const api = await library;
    if (!Object.hasOwn(api, method) || !Array.isArray(args)) throw new TypeError("Invalid library call");
    self.postMessage({ id, value: await api[method](...args) });
  } catch (error) {
    self.postMessage({ id, error: { name: error.name, message: error.message } });
  }
};
