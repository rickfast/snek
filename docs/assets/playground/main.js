const root = document.querySelector("#snek-playground");
if (root) {
  const source = root.querySelector("#snek-source"), input = root.querySelector("#snek-input");
  const run = root.querySelector("#snek-run"), stop = root.querySelector("#snek-stop");
  const download = root.querySelector("#snek-download"), examples = root.querySelector("#snek-example");
  const output = root.querySelector("#snek-output"), status = root.querySelector("#snek-status");
  const details = root.querySelector("#snek-details");
  let worker, timer, binary, exampleRequest = 0;
  function finish(message) {
    clearTimeout(timer);
    worker?.terminate();
    worker = undefined;
    run.disabled = false;
    stop.disabled = true;
    examples.disabled = false;
    status.textContent = message;
    root.dataset.state = "idle";
  }
  function deadline(phase) {
    clearTimeout(timer);
    const seconds = phase === "loading" ? 30 : phase === "compiling" ? 15 : 3;
    timer = setTimeout(() => finish(`${phase} timed out after ${seconds} seconds. Edit the program and try again.`), seconds * 1000);
  }
  async function loadExample() {
    const request = ++exampleRequest;
    run.disabled = true;
    try {
      const response = await fetch(new URL(`./generated/${examples.value}.snek`, import.meta.url));
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const text = await response.text();
      if (request !== exampleRequest) return;
      source.value = text;
      input.value = examples.value === "streams" ? "Hello from stdin, 🐍!\n" : "";
      binary = undefined;
      download.disabled = true;
      output.textContent = "";
      details.textContent = "";
      status.textContent = "Ready. Edit the source, then run it.";
      root.dataset.state = "idle";
      run.disabled = false;
    } catch (error) {
      status.textContent = `Unable to load example: ${error.message}. You can still write and run your own program.`;
      run.disabled = false;
    }
  }
  run.onclick = () => {
    if (worker) return;
    run.disabled = true;
    stop.disabled = false;
    examples.disabled = true;
    download.disabled = true;
    binary = undefined;
    output.textContent = "";
    details.textContent = "";
    root.dataset.state = "busy";
    status.textContent = "Starting worker…";
    deadline("loading");
    try {
      worker = new Worker(new URL("./worker.js", import.meta.url), { type: "module" });
      const active = worker;
      worker.onmessage = ({ data }) => {
        if (worker !== active) return;
        if (data.type === "phase") { status.textContent = data.message; deadline(data.phase); }
        else if (data.type === "compiled") {
          binary = data.binary;
          download.disabled = false;
          details.textContent = `${binary.length.toLocaleString()} bytes of Wasm · compiled in ${Math.round(data.milliseconds)} ms`;
        } else if (data.type === "result") {
          output.textContent = data.stdout + data.stderr;
          finish(data.code === 0 ? "Finished." : `Program exited with code ${data.code}.`);
        } else if (data.type === "error") {
          output.textContent = data.message;
          finish("Could not complete the run.");
        }
      };
      worker.onerror = event => {
        if (worker !== active) return;
        output.textContent = event.message || "Unable to start the worker. Check browser support and Content Security Policy.";
        finish("Worker failed.");
      };
      worker.postMessage({ source: source.value, stdin: input.value });
    } catch (error) {
      output.textContent = error.message;
      finish("Worker failed.");
    }
  };
  stop.onclick = () => finish("Stopped. You can edit and run again.");
  source.addEventListener("keydown", event => {
    if ((event.ctrlKey || event.metaKey) && event.key === "Enter" && !run.disabled) { event.preventDefault(); run.click(); }
  });
  download.onclick = () => {
    if (!binary) return;
    const url = URL.createObjectURL(new Blob([binary], { type: "application/wasm" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = "playground.wasm";
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  examples.onchange = loadExample;
  window.addEventListener("pagehide", () => finish("Stopped."), { once: true });
  loadExample();
}
