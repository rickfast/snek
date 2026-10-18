"""Execute the checked-in Vite example in dev and production, with both compilers."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
from queue import Queue, Empty
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from snekc import driver
from check_library import LIST_SOURCE, LIST_EXPORTS, NESTED_LIST_SOURCE, NESTED_LIST_EXPORTS
from check_library import LIST_CALLBACK_SOURCE, LIST_CALLBACK_EXPORTS

EXAMPLE = ROOT / "examples/vite"
SERVER = '''
import {createServer, preview} from "vite";
const config = {base: process.env.TEST_BASE, logLevel: "error", cacheDir: ".vite-test",
  server: {host: "127.0.0.1", port: 0}, preview: {host: "127.0.0.1", port: 0}};
const server = process.env.TEST_DEV === "1" ? await createServer(config) : await preview(config);
if (process.env.TEST_DEV === "1") await server.listen();
console.log(JSON.stringify({url: server.resolvedUrls.local[0]}));
'''

TYPES = '''
import {type SnekLibrary as ListCallbackLibrary} from "./generated/list-callbacks.js";
declare const listCallbacks: ListCallbackLibrary;
const transformed: SnekList<string> = listCallbacks.apply(items => items, listCallbacks.words());
const asyncTransformed: Promise<SnekList<SnekList<string>>> = listCallbacks.apply_async(async items => items, listCallbacks.rows());
// @ts-expect-error callback results require handles, not arrays
listCallbacks.apply(items => items.toArray(), listCallbacks.words());
// @ts-expect-error async callback result element types must match
listCallbacks.apply_async(async items => items.get(0), listCallbacks.rows());
// @ts-expect-error callbacks accept their declared list element type
listCallbacks.apply((items: SnekList<number>) => items, listCallbacks.words());
import {type SnekLibrary as NestedLibrary} from "./generated/nested.js";
declare const nested: NestedLibrary;
const rows: SnekList<SnekList<string>> = nested.rows();
const child: SnekList<string> = rows.get(0);
rows.append(child);
const shallow: SnekList<string>[] = rows.toArray();
// @ts-expect-error nested lists require handles, not arrays
rows.append(["array"]);
// @ts-expect-error nesting depth is checked
rows.append(rows);
import {type SnekLibrary as ListLibrary, type SnekList} from "./generated/lists.js";
declare const lists: ListLibrary;
const words: SnekList<string> = lists.words();
const snapshot: string[] = words.toArray();
words.append("typed"); words.set(-1, words.get(0));
const popped: string = words.pop();
const delayed: Promise<SnekList<string>> = lists.delayed(words);
// @ts-expect-error mutable lists are invariant, even for nullable element types
const widened: SnekList<object | null> = lists.objects(document.body);
// @ts-expect-error list elements are typed
words.append(42);
// @ts-expect-error length is read-only
words.length = 0;
// @ts-expect-error arrays are not handles
lists.echo(["array"]);
// @ts-expect-error list element types must match
lists.echo(lists.numbers());
import {instantiate, type SnekLibrary} from "./generated/program.js";
declare const library: SnekLibrary;
const result: Promise<string> = library.load("url");
const cancellable: Promise<string> = library.load("url", {signal: new AbortController().signal});
// @ts-expect-error cancellation requires an AbortSignal
library.load("url", {signal: {}});
// @ts-expect-error synchronous exports do not take call options
library.greet("name", {signal: new AbortController().signal});
const exact: bigint = library.twice(1n);
const opaque: object = library.identity(document.body);
const greeting: string = library.apply(library.make_greeter(), "typed");
library.notify(text => { console.log(text); }, "typed");
const asyncGreeting: Promise<string> = library.apply_async(async text => text, "typed");
// @ts-expect-error async callbacks must return a PromiseLike
library.apply_async(text => text, "typed");
// @ts-expect-error async callback result types are checked
library.apply_async(async text => 42, "typed");
// @ts-expect-error callbacks must return their declared value
library.apply(text => 42, "typed");
// @ts-expect-error callback parameters are typed
library.apply((value: number) => String(value), "typed");
// @ts-expect-error callbacks are synchronous
library.apply(async text => text, "typed");
// @ts-expect-error None callbacks cannot return a Promise
library.notify(async text => { console.log(text); }, "typed");
// @ts-expect-error HostObject rejects primitive values
library.identity(42);
// @ts-expect-error i64 is bigint, not number
library.twice(1);
// @ts-expect-error async exports do not return a bare string
const wrong: string = library.load("url");
// @ts-expect-error required host imports cannot be omitted
instantiate("url");
// @ts-expect-error async host imports must return a PromiseLike
instantiate("url", {imports: {fetch_text: () => "bare"}});
// @ts-expect-error host result must be a string
instantiate("url", {imports: {fetch_text: async () => 42}});
'''


class BundlerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        if not (EXAMPLE / "node_modules/vite/package.json").is_file():
            raise RuntimeError("run npm ci --prefix examples/vite first")
        directory = tempfile.TemporaryDirectory(prefix="snek-vite-")
        cls.addClassCleanup(directory.cleanup)
        cls.root = Path(directory.name) / "app"
        shutil.copytree(EXAMPLE, cls.root, ignore=shutil.ignore_patterns("node_modules", "dist", "generated"))
        (cls.root / "node_modules").symlink_to(EXAMPLE / "node_modules", target_is_directory=True)
        (cls.root / "src/generated").mkdir()
        (cls.root / "src/typecheck.ts").write_text(TYPES)
        cls.compiler = os.environ.get("SNEK_COMPILER", "python")
        cls.node = os.environ.get("SNEK_NODE", "node")
        playwright = sync_playwright().start()
        cls.addClassCleanup(playwright.stop)
        engine = os.environ.get("SNEK_BROWSER", "chromium")
        if engine not in ("chromium", "firefox", "webkit"):
            raise ValueError("SNEK_BROWSER must be chromium, firefox, or webkit")
        cls.browser = getattr(playwright, engine).launch()
        cls.addClassCleanup(cls.browser.close)
        print(f"Bundler browser: {engine} {cls.browser.version}; compiler: {cls.compiler}", flush=True)

    def compile(self, optimize):
        source = self.root / "lists.py"
        source.write_text(LIST_SOURCE)
        driver.build(source, self.root / "src/generated/lists.wasm", optimize=optimize,
                     compiler=self.compiler, library=True, exports=LIST_EXPORTS)
        source = self.root / "nested.py"
        source.write_text(NESTED_LIST_SOURCE)
        driver.build(source, self.root / "src/generated/nested.wasm", optimize=optimize,
                     compiler=self.compiler, library=True, exports=NESTED_LIST_EXPORTS)
        source = self.root / "list_callbacks.py"
        source.write_text(LIST_CALLBACK_SOURCE)
        driver.build(source, self.root / "src/generated/list-callbacks.wasm", optimize=optimize,
                     compiler=self.compiler, library=True, exports=LIST_CALLBACK_EXPORTS)
        driver.build(self.root / "program.py", self.root / "src/generated/program.wasm",
                     optimize=optimize, compiler=self.compiler, library=True,
                     exports=["greet", "twice", "load", "identity", "apply", "make_greeter", "notify", "apply_async"])
        self.run_node("node_modules/typescript/bin/tsc", "--noEmit")

    def run_node(self, *args):
        result = subprocess.run([self.node, *args], cwd=self.root, capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    @contextmanager
    def server(self, base, dev):
        with tempfile.TemporaryFile(mode="w+") as errors:
            process = subprocess.Popen([self.node, "--input-type=module", "-e", SERVER],
                                       cwd=self.root, stdout=subprocess.PIPE, stderr=errors, text=True,
                                       env={**os.environ, "TEST_BASE": base, "TEST_DEV": "1" if dev else "0"})
            lines = Queue()
            thread = threading.Thread(target=lambda: lines.put(process.stdout.readline()), daemon=True)
            thread.start()
            try:
                try:
                    line = lines.get(timeout=30)
                except Empty:
                    self.fail("Vite server did not start within 30 seconds")
                if not line:
                    errors.seek(0)
                    self.fail("Vite server failed: " + errors.read())
                yield json.loads(line)["url"]
            finally:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
                thread.join(timeout=5)
                process.stdout.close()

    def exercise(self, url, base, dev):
        context = self.browser.new_context()
        try:
            page = context.new_page()
            page.set_default_timeout(15000)
            errors, responses = [], []
            page.on("pageerror", lambda error: errors.append(str(error)))
            context.on("response", lambda response: responses.append((response.url, response.status)))
            page.goto(url)
            page.wait_for_function('document.documentElement.dataset.status === "ready"')
            self.assertEqual(page.locator("#greeting").text_content(), "Hello, Vite 🐍!")
            self.assertEqual(page.locator("#integer").text_content(), "18014398509481986")
            for _ in range(2):
                page.locator("#load").click()
                page.wait_for_function('!document.querySelector("#load").disabled')
                self.assertEqual(page.locator("#result").text_content(), "HELLO FROM BUNDLED ASYNC SNEK, STRASSE 🐍!")
                page.locator("#worker").click()
                page.wait_for_function('!document.querySelector("#worker").disabled')
                self.assertEqual(page.locator("#worker-result").text_content(), "Hello, Vite worker 🐍!")
            self.assertEqual(errors, [])
            self.assertFalse([item for item in responses if item[1] >= 400], responses)
            wasm = [url for url, status in responses if ".wasm" in url and status == 200]
            self.assertTrue(wasm, responses)
            if not dev:
                self.assertTrue(all(base + "assets/program-" in url for url in wasm), wasm)
        finally:
            context.close()

    def test_development_server(self):
        self.compile(False)
        with self.server("/snek-test/", True) as url:
            self.exercise(url, "/snek-test/", True)

    def test_production_builds(self):
        for optimize in (False, True):
            for base in ("/snek-test/", "./"):
                with self.subTest(optimize=optimize, base=base):
                    self.compile(optimize)
                    self.run_node("node_modules/vite/bin/vite.js", "build", "--base", base)
                    assets = self.root / "dist/assets"
                    self.assertTrue(list(assets.glob("program-*.wasm")))
                    self.assertTrue(list(assets.glob("worker-*.js")))
                    # Relocate the relative build without rebuilding its asset URLs.
                    served_base = base if base != "./" else "/relocated/deep/"
                    with self.server(served_base, False) as url:
                        self.exercise(url, served_base, False)


if __name__ == "__main__":
    unittest.main()
