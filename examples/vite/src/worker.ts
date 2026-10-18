import { instantiate } from "./generated/program.js";
import wasmUrl from "./generated/program.wasm?url&no-inline";
import { imports } from "./host";

// This worker owns its own independent instance. Messages carry copied values.
let library: ReturnType<typeof instantiate> | undefined;
self.onmessage = async ({ data }: MessageEvent<string>) => {
  try {
    library ??= instantiate(wasmUrl, { imports });
    self.postMessage({ value: (await library).greet(data) });
  } catch (error) {
    self.postMessage({ error: String(error) });
  }
};
