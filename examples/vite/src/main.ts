import { instantiate } from "./generated/program.js";
import wasmUrl from "./generated/program.wasm?url&no-inline";
import messageUrl from "./message.txt?url&no-inline";
import { imports } from "./host";

const load = document.querySelector<HTMLButtonElement>("#load")!;
const workerButton = document.querySelector<HTMLButtonElement>("#worker")!;
const greeting = document.querySelector<HTMLElement>("#greeting")!;
const result = document.querySelector<HTMLElement>("#result")!;
const workerResult = document.querySelector<HTMLElement>("#worker-result")!;

async function start() {
  const library = await instantiate(wasmUrl, { imports });
  if (library.identity(greeting) !== greeting) throw new Error("DOM object identity was lost");
  greeting.textContent = library.apply(library.make_greeter(), "Vite 🐍");
  document.querySelector("#integer")!.textContent = library.twice(9007199254740993n).toString();
  load.disabled = false;
  load.onclick = async () => {
    load.disabled = true; // Keep this button's requests in order.
    try {
      result.textContent = await library.load(messageUrl);
    } catch (error) {
      result.textContent = String(error);
    } finally {
      load.disabled = false;
    }
  };

  const worker = new Worker(new URL("./worker.ts", import.meta.url), { type: "module" });
  workerButton.disabled = false;
  workerButton.onclick = () => {
    workerButton.disabled = true;
    worker.postMessage("Vite worker 🐍");
  };
  worker.onmessage = ({ data }) => {
    workerResult.textContent = data.error ?? data.value;
    workerButton.disabled = false;
  };
  worker.onerror = (event) => {
    workerResult.textContent = event.message;
    workerButton.disabled = true;
  };
  window.addEventListener("pagehide", () => worker.terminate(), { once: true });
  import.meta.hot?.dispose(() => worker.terminate());
  document.documentElement.dataset.status = "ready";
}

start().catch(error => {
  greeting.textContent = String(error);
  document.documentElement.dataset.status = "error";
});
