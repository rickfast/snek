"""Real browser-engine library tests. No unsupported-engine skips or version guesses."""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from snekc import driver
from check_library import SOURCE, EXPORTS, HOST_SOURCE, HOST_EXPORTS, ASYNC_SOURCE, ASYNC_EXPORTS
from check_library import OBJECT_SOURCE, OBJECT_EXPORTS, CALLBACK_SOURCE, CALLBACK_EXPORTS


class Handler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


class BrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        directory = tempfile.TemporaryDirectory(prefix="snek-browser-")
        cls.addClassCleanup(directory.cleanup)
        root = Path(directory.name)
        cls.root = root
        compiler = os.environ.get("SNEK_COMPILER", "python")
        source = root / "fixture.py"
        source.write_text(SOURCE + '\ndef upper(value: str) -> str:\n    return value.upper()\n')
        for name, optimize in (("raw", False), ("optimized", True)):
            driver.build(source, root / (name + ".wasm"), optimize=optimize,
                         compiler=compiler, library=True, exports=[*EXPORTS, "upper"])
        source.write_text(HOST_SOURCE)
        for name, optimize in (("host", False), ("host-optimized", True)):
            driver.build(source, root / (name + ".wasm"), optimize=optimize,
                         compiler=compiler, library=True, exports=HOST_EXPORTS)
        source.write_text(ASYNC_SOURCE)
        for name, optimize in (("async", False), ("async-optimized", True)):
            driver.build(source, root / (name + ".wasm"), optimize=optimize,
                         compiler=compiler, library=True, exports=ASYNC_EXPORTS)
        source.write_text(OBJECT_SOURCE)
        for name, optimize in (("objects", False), ("objects-optimized", True)):
            driver.build(source, root / (name + ".wasm"), optimize=optimize,
                         compiler=compiler, library=True, exports=OBJECT_EXPORTS)
        driver.build(ROOT / "examples/library/greetings.py", root / "greetings.wasm", optimize=False,
                     compiler=compiler, library=True, exports=["greet", "add", "twice"])
        source.write_text(CALLBACK_SOURCE)
        for name, optimize in (("callbacks", False), ("callbacks-optimized", True)):
            driver.build(source, root / (name + ".wasm"), optimize=optimize,
                         compiler=compiler, library=True, exports=CALLBACK_EXPORTS)
        shutil.copyfile(ROOT / "examples/library/worker.mjs", root / "worker.mjs")
        driver.build(ROOT / "examples/library/host_demo.py", root / "host_demo.wasm", optimize=False,
                     compiler=compiler, library=True, exports=["increment"])
        shutil.copyfile(ROOT / "examples/library/host.html", root / "host.html")
        driver.build(ROOT / "examples/library/async_demo.py", root / "async_demo.wasm", optimize=False,
                     compiler=compiler, library=True, exports=["load"])
        for name in ("async.html", "message.txt"):
            shutil.copyfile(ROOT / "examples/library" / name, root / name)
        (root / "index.html").write_text('<!doctype html><meta charset="utf-8"><title>Snek browser tests</title>')
        server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=root))
        cls.addClassCleanup(server.server_close)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        cls.addClassCleanup(lambda: (server.shutdown(), thread.join()))
        cls.url = f"http://127.0.0.1:{server.server_port}"
        playwright = sync_playwright().start()
        cls.addClassCleanup(playwright.stop)
        engine = os.environ.get("SNEK_BROWSER", "chromium")
        if engine not in ("chromium", "firefox", "webkit"):
            raise ValueError("SNEK_BROWSER must be chromium, firefox, or webkit")
        cls.browser = getattr(playwright, engine).launch()
        cls.addClassCleanup(cls.browser.close)
        print(f"Browser: {engine} {cls.browser.version}; compiler: {compiler}", flush=True)

    def setUp(self):
        context = self.browser.new_context()
        self.addCleanup(context.close)
        self.page = context.new_page()
        self.page.set_default_timeout(15000)
        self.page.goto(self.url)

    def test_raw_and_optimized_unicode_types_errors(self):
        for target in ("raw", "optimized"):
            with self.subTest(target=target):
                result = self.page.evaluate('''async target => {
                  const {instantiate, assertSupported, SnekError} = await import(`./${target}.js`);
                  assertSupported();
                  let output = "";
                  const lib = await instantiate(new URL(`./${target}.wasm`, location.href), {stdout: t => output += t});
                  const large = "🐍".repeat(100000);
                  const failures = [];
                  for (const call of [() => lib.add(2147483648, 0), () => lib.big(1n << 63n), () => lib.flip(1)]) {
                    try {call();} catch (e) {failures.push(e.name);}
                  }
                  if (failures.join() !== "RangeError,RangeError,TypeError") throw new Error("boundary checks failed");
                  let message;
                  try {lib.fail();} catch (e) {if (!(e instanceof SnekError)) throw e; message = e.message;}
                  lib.show("browser");
                  return [lib.add(20, 22), lib.big(9007199254740993n).toString(), lib.flip(true),
                    lib.text("\\ufeff中", "🐍"), lib.count(large), lib.text(large, large).length,
                    lib.upper("Straße"), message, output, lib.add(1, 2), Object.keys(lib)];
                }''', target)
                self.assertEqual(result[:-1], [42, "9007199254740993", False, "\ufeff中🐍", 100000,
                                               400000, "STRASSE", "ValueError: bad 🐍", "initialized\nbrowser\n", 3])
                self.assertEqual(result[-1], [*EXPORTS, "upper"])

    def test_synchronous_host_imports_and_dom(self):
        for target in ("host", "host-optimized"):
            with self.subTest(target=target):
                result = self.page.evaluate('''async target => {
                  const {instantiate} = await import(`./${target}.js`);
                  const node = document.createElement("output");
                  document.body.append(node);
                  let mode = "ok", lib;
                  const imports = {
                    render: text => {node.textContent = text;},
                    transform: (a, b) => a + b,
                    numbers: (a, b, c, d, e) => {if (!e) throw Error("bool"); return b;},
                    get_text: () => {
                      if (mode === "reentry") return lib.upper();
                      if (mode === "async") return Promise.resolve("later");
                      return "Straße";
                    },
                    integer: () => 42, boolean: () => true,
                    single: () => 1.1, double: () => Infinity
                  };
                  lib = await instantiate(`./${target}.wasm`, {imports});
                  const initial = node.textContent;
                  lib.render(lib.upper());
                  const errors = [];
                  for (mode of ["reentry", "async"]) {
                    try {lib.guarded();} catch (e) {errors.push(e.message);}
                  }
                  mode = "ok";
                  const values = [initial, node.textContent, lib.transform("\\ufeff🐍", "中"),
                    lib.numbers(1, 9007199254740993n, 1.1, 2.2, true).toString(), lib.upper(), errors];
                  node.remove();
                  return values;
                }''', target)
                self.assertEqual(result[:-1], ["ready 🐍", "STRASSE", "\ufeff🐍中", "9007199254740993", "STRASSE"])
                self.assertEqual(len(result[-1]), 2)
                self.assertIn("not reentrant", result[-1][0])
                self.assertIn("must be synchronous", result[-1][1])

    def test_host_example(self):
        self.page.goto(self.url + "/host.html")
        self.page.wait_for_function('document.documentElement.dataset.status === "ready"')
        self.page.locator("#name").fill("Snek 🐍")
        self.page.locator("#increment").click()
        self.assertEqual(self.page.locator("#result").text_content(), "Hello, Snek 🐍! Clicks: 1")
        self.page.locator("#increment").click()
        self.assertEqual(self.page.locator("#result").text_content(), "Hello, Snek 🐍! Clicks: 2")

    def test_opaque_dom_objects(self):
        for target in ("objects", "objects-optimized"):
            with self.subTest(target=target):
                result = self.page.evaluate('''async target => {
                  const {instantiate} = await import(`./${target}.js`);
                  const node = document.createElement("output"), other = document.createElement("span");
                  document.body.append(node);
                  const lib = await instantiate(`./${target}.wasm`, {imports: {
                    lookup: name => name === "missing" ? null : node,
                    touch: value => {value.textContent = "DOM from Snek 🐍";},
                    resolve: async value => value,
                    wait: () => new Promise(resolve => setTimeout(resolve, 1))
                  }});
                  const values = [lib.echo(node) === node, lib.find("node") === node, node.textContent,
                    lib.find("missing"), lib.optional(null), lib.same(node, node), lib.same(node, other),
                    lib.collections(node, other), lib.closure(node) === node,
                    await lib.hold(node) === node, await lib.optional_async(null)];
                  node.remove();
                  return values;
                }''', target)
                self.assertEqual(result, [True, True, "DOM from Snek 🐍", None, None, True, False, True, True, True, None])

    def test_typed_callbacks_and_deferred_dom_events(self):
        for target in ("callbacks", "callbacks-optimized"):
            with self.subTest(target=target):
                result = self.page.evaluate('''async target => {
                  const {instantiate} = await import(`./${target}.js`);
                  const button = document.createElement("button");
                  document.body.append(button);
                  let output = "";
                  const supplied = text => text + "🐍";
                  const lib = await instantiate(`./${target}.wasm`, {stdout: text => output += text, imports: {
                    store: callback => button.addEventListener("click", () => callback("Straße")),
                    get_callback: () => supplied, get_async_callback: async () => supplied
                  }});
                  lib.install("event: ");
                  button.click();
                  const closure = lib.make("prefix: "), count = lib.counter(40);
                  const values = [output, lib.apply(supplied, "中"), lib.echo(supplied) === supplied,
                    lib.echo(closure) === closure, closure("Straße"), count(), count(),
                    await lib.deferred(supplied, "中"), (await lib.deferred_make("async: "))("Straße"),
                    lib.object_callback()(button) === button];
                  let error;
                  try {lib.apply(async text => text, "bad");} catch (e) {error = e.message;}
                  values.push(error, closure("ok"));
                  button.remove();
                  return values;
                }''', target)
                self.assertEqual(result[:-2], ["event: Straße\n", "中🐍", True, True,
                    "prefix: STRASSE", 41, 42, "中🐍", "async: STRASSE", True])
                self.assertIn("must be synchronous", result[-2])
                self.assertEqual(result[-1], "prefix: OK")

    def test_async_host_imports(self):
        for target in ("async", "async-optimized"):
            with self.subTest(target=target):
                result = self.page.evaluate('''async target => {
                  const {instantiate} = await import(`./${target}.js`);
                  const events = [];
                  let mode = "ok", resolvePending;
                  const lib = await instantiate(`./${target}.wasm`, {imports: {
                    read_text: value => {
                      if (mode === "pending") return new Promise(resolve => {resolvePending = resolve;});
                      if (mode === "error") return Promise.reject(new Error("browser rejection"));
                      return Promise.resolve(value);
                    },
                    wait: async () => {events.push("wait");}, number: async value => value,
                    scalar: async (a, b, c, d) => c, render: text => {events.push(text);}
                  }});
                  mode = "pending";
                  const loading = lib.load("url");
                  const concurrent = await lib.plain(1);
                  resolvePending("Straße 🐍");
                  const value = await loading;
                  mode = "error";
                  const error = await lib.recover();
                  mode = "ok";
                  return [value, concurrent, error, await lib.flow("done"), events,
                    (await lib.number(9007199254740993n)).toString(), await lib.plain(41)];
                }''', target)
                self.assertEqual(result[0], "STRASSE 🐍")
                self.assertEqual(result[1], 2)
                self.assertIn("browser rejection", result[2])
                self.assertEqual(result[3:], ["DONE!", ["done", "wait", "cleaned", "done", "wait"], "9007199254740993", 42])

    def test_concurrent_async_host_results(self):
        for target in ("async", "async-optimized"):
            with self.subTest(target=target):
                result = self.page.evaluate('''async target => {
                  const {instantiate} = await import(`./${target}.js`);
                  const requests = new Map(), events = [];
                  const lib = await instantiate(`./${target}.wasm`, {imports: {
                    read_text: value => new Promise((resolve, reject) => requests.set(value, {resolve, reject})),
                    wait: async () => {}, number: async value => value,
                    scalar: async (a, b, c, d) => c, render: text => {events.push(text);}
                  }});
                  const first = lib.load("first"), second = lib.load("second"), third = lib.load("third");
                  const failed = third.catch(error => error.message);
                  const started = [...requests.keys()];
                  requests.get("second").resolve("Straße 🐍".repeat(1000));
                  const secondResult = await second;
                  requests.get("third").reject(new Error("isolated"));
                  const error = await failed;
                  const sync = lib.sync(42);
                  requests.get("first").resolve("first");
                  return [started, secondResult, error, sync, await first, events];
                }''', target)
                self.assertEqual(result[0], ["first", "second", "third"])
                self.assertEqual(result[1], "STRASSE 🐍" * 1000)
                self.assertIn("isolated", result[2])
                self.assertEqual(result[3:], [42, "FIRST", ["done", "done", "done"]])

    def test_async_fetch_example(self):
        self.page.goto(self.url + "/async.html")
        self.page.wait_for_function('document.documentElement.dataset.status === "ready"')
        self.page.locator("#load").click()
        self.page.wait_for_function('document.querySelector("#result").textContent.includes("AWAITED FETCH")')
        self.assertEqual(self.page.locator("#result").text_content(), "HELLO FROM AN AWAITED FETCH, STRASSE 🐍!")
        result = self.page.evaluate('''async () => {
          const {instantiate} = await import("./async_demo.js");
          const lib = await instantiate("./async_demo.wasm", {imports: {fetch_text: async url => {
            const response = await fetch(url);
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            return response.text();
          }}});
          return lib.load("./missing.txt");
        }''')
        self.assertIn("Could not load: Host import fetch_text: HTTP 404", result)

    def test_bytes_response_module_and_wrong_mime(self):
        result = self.page.evaluate('''async () => {
          const {instantiate} = await import("./raw.js");
          const bytes = await (await fetch("./raw.wasm")).arrayBuffer();
          const sources = [bytes, new Uint8Array(bytes), await WebAssembly.compile(bytes),
            new Response(bytes, {headers: {"Content-Type": "application/octet-stream"}})];
          return await Promise.all(sources.map(async s => (await instantiate(s)).add(2, 3)));
        }''')
        self.assertEqual(result, [5, 5, 5, 5])

    def test_loading_failures_are_actionable(self):
        result = self.page.evaluate('''async () => {
          const {instantiate, SnekLoadError} = await import("./raw.js");
          const errors = [];
          for (const source of ["./missing.wasm", "./index.html", new Uint8Array([0,97,115,109,1,0,0,0])]) {
            try {await instantiate(source); throw new Error("unexpected success");}
            catch (e) {if (!(e instanceof SnekLoadError)) throw e; errors.push(e.message);}
          }
          const saved = globalThis.fetch;
          globalThis.fetch = async () => {throw new TypeError("network blocked");};
          try {await instantiate("./raw.wasm");} catch (e) {errors.push(e.message, e.cause.message);}
          finally {globalThis.fetch = saved;}
          return errors;
        }''')
        for fragment, value in zip(("HTTP 404", "HTML", "exports", "CORS", "network blocked"), result):
            self.assertIn(fragment, value)
        self.assertEqual(len(result), 5)

    def test_unsupported_features_fail_before_fetch(self):
        result = self.page.evaluate('''async () => {
          const {instantiate, SnekCompatibilityError} = await import("./raw.js");
          const saved = globalThis.WebAssembly, fetch = globalThis.fetch;
          const errors = [];
          globalThis.fetch = () => {throw new Error("must not fetch");};
          try {
            for (const value of [undefined, {validate: () => false}, {validate: b => b.length === 30}]) {
              globalThis.WebAssembly = value;
              try {await instantiate("./raw.wasm"); throw new Error("unexpected success");}
              catch (e) {if (!(e instanceof SnekCompatibilityError)) throw e; errors.push(e.message);}
            }
          } finally {globalThis.WebAssembly = saved; globalThis.fetch = fetch;}
          return errors;
        }''')
        for fragment, value in zip(("requires WebAssembly", "GC", "try_table"), result):
            self.assertIn(fragment, value)
        self.assertEqual(len(result), 3)

    def test_module_worker_and_error_recovery(self):
        result = self.page.evaluate('''async () => {
          const worker = new Worker(new URL("./worker.mjs", location.href), {type: "module"});
          const pending = new Map();
          worker.onmessage = ({data}) => {pending.get(data.id).resolve(data); pending.delete(data.id);};
          worker.onerror = event => {for (const p of pending.values()) p.reject(new Error(event.message));};
          const call = (id, method, args) => new Promise((resolve, reject) => {
            const timeout = setTimeout(() => reject(new Error("worker call timed out")), 10000);
            pending.set(id, {resolve: value => {clearTimeout(timeout); resolve(value);},
              reject: error => {clearTimeout(timeout); reject(error);}});
            worker.postMessage({id, method, args});
          });
          try {
            const values = await Promise.all([call(1, "greet", ["worker 🐍"]), call(2, "twice", [9007199254740993n])]);
            const bad = await call(3, "missing", []);
            const recovered = await call(4, "add", [20, 22]);
            return [values[0].value, values[1].value.toString(), bad.error.name, recovered.value, crossOriginIsolated];
          } finally {worker.terminate();}
        }''')
        self.assertEqual(result, ["Hello, worker 🐍!", "18014398509481986", "TypeError", 42, False])


if __name__ == "__main__":
    unittest.main()
