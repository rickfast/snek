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
from check_library import CLASS_VALUE_SOURCE, CLASS_VALUE_EXPORTS
from check_library import CALLBACK_LIST_SOURCE, CALLBACK_LIST_EXPORTS
from check_library import HIGHER_CALLBACK_SOURCE, HIGHER_CALLBACK_EXPORTS
from check_library import DICT_SOURCE, DICT_EXPORTS
from check_library import TUPLE_SOURCE, TUPLE_EXPORTS
from check_library import OPTIONAL_SOURCE, OPTIONAL_EXPORTS

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
import {type SnekLibrary as OptionalLibrary} from "./generated/optional.js";
declare const optional: OptionalLibrary;
const optionalText: string | null = optional.echo(null);
const optionalAsync: Promise<string | null> = optional.upper_async();
optional.maybe_list(null); optional.maybe_dict(null); optional.maybe_tuple(null); optional.maybe_callback(null);
const maybeList = optional.maybe_list(optional.make_list());
if (maybeList !== null) maybeList.append(null);
const nullableDict = optional.make_dict(); nullableDict.set(null, null);
const maybeValue: string | null | undefined = nullableDict.get(null);
optional.keys().set(optional.make_key(null), "nullable key");
optional.keys().set(null, "absent tuple");
optional.call_optional(value => value, null);
optional.apply_async(async value => value, null);
const nullableCallback = optional.make_callback();
optional.factory(enabled => enabled ? nullableCallback : null, true);
// @ts-expect-error nullable result requires narrowing
const requiredString: string = optional.echo("text");
// @ts-expect-error nullable handle requires narrowing
maybeList.append(null);
// @ts-expect-error undefined is not None/null
optional.echo(undefined);
// @ts-expect-error nullable callback results still require a string or null
optional.call_optional(value => 42, null);
// @ts-expect-error async callback results cannot be undefined
optional.apply_async(async value => undefined, null);
import {type SnekLibrary as TupleLibrary, type SnekTuple} from "./generated/tuples.js";
declare const tuples: TupleLibrary;
const pair = tuples.pair("typed");
const tupleText: string = pair.get(0);
const tupleNumber: number = pair.get(1);
const tupleLength: 2 = pair.length;
const tupleArray: readonly [string, number] = pair.toArray();
const keyed = tuples.keyed(pair);
const keyedValue: string | undefined = keyed.get(tuples.pair("equal"));
keyed.set(pair, "typed");
const keyText: string = keyed.entries()[0][0].get(0);
// @ts-expect-error dictionary tuple-key shapes are exact
keyed.get(tuples.singleton());
// @ts-expect-error tuple-key copies are not handles
keyed.set(pair.toArray(), "wrong");
tuples.apply(value => value, pair);
tuples.apply_async(async value => value, pair);
tuples.composed().get(0).append("live");
// @ts-expect-error tuple copies are readonly
pair.toArray()[0] = "changed";
// @ts-expect-error tuple field types are positional
const wrongField: string = pair.get(1);
// @ts-expect-error arrays cannot impersonate tuple handles
tuples.echo(["typed", 7]);
// @ts-expect-error tuple field types must match exactly, without widening
const widenedTuple: SnekTuple<readonly [string | number, number]> = pair;
// @ts-expect-error tuple shape must match
tuples.echo(tuples.singleton());
// @ts-expect-error callback results require handles, not tuple copies
tuples.apply(value => value.toArray(), pair);
// @ts-expect-error length is readonly
pair.length = 3;
import {type SnekLibrary as DictLibrary, type SnekDict} from "./generated/dicts.js";
declare const dicts: DictLibrary;
const mapping: SnekDict<string, string> = dicts.words();
const maybeWord: string | undefined = mapping.get("key");
const entries: [string, string][] = mapping.entries();
mapping.set("key", "value"); mapping.delete("key"); mapping.clear();
dicts.integers().set(1, 2n);
dicts.apply(items => items, mapping);
dicts.apply_async(async items => items, mapping);
// @ts-expect-error dictionary key types are checked
mapping.get(1);
// @ts-expect-error dictionary value types are checked
mapping.set("key", 1);
// @ts-expect-error missing keys produce undefined
const definitelyWord: string = mapping.get("key");
// @ts-expect-error JS Maps cannot impersonate Snek dictionaries
dicts.echo(new Map<string, string>());
// @ts-expect-error dictionaries are invariant in their values
const widenedValues: SnekDict<string, string | null> = mapping;
// @ts-expect-error dictionaries are invariant in their keys
const widenedKeys: SnekDict<string | number, string> = mapping;
// @ts-expect-error async dictionary callback results require handles
dicts.apply_async(async items => new Map(), mapping);
import {type SnekLibrary as HigherLibrary} from "./generated/higher-callbacks.js";
declare const higher: HigherLibrary;
const decorated: string = higher.decorator("!")(text => text)("typed");
const factory: PromiseLike<(arg: string) => string> = higher.async_factory()("prefix");
higher.use(prefix => text => prefix + text, "prefix", "typed");
higher.use_async(async prefix => text => prefix + text, "prefix", "typed");
higher.async_consumer()(async text => text, "typed");
higher.async_decorator()(async text => text);
// @ts-expect-error nested callback parameter types are checked
higher.decorator("!")((value: number) => String(value));
// @ts-expect-error factories must return functions
higher.use(prefix => prefix, "prefix", "typed");
// @ts-expect-error factory result function types are checked
higher.use(prefix => text => 42, "prefix", "typed");
// @ts-expect-error async factories require a PromiseLike
higher.use_async(prefix => text => text, "prefix", "typed");
// @ts-expect-error nested async callbacks require a PromiseLike
higher.async_consumer()(text => text, "typed");
import {type SnekLibrary as CallbackListLibrary} from "./generated/callback-lists.js";
declare const callbackLists: CallbackListLibrary;
const functions = callbackLists.sync_list("prefix");
const called: string = functions.get(0)("text");
functions.append(text => text);
const asyncFunctions = callbackLists.async_list("prefix");
asyncFunctions.append(async text => text);
const asyncCalled: PromiseLike<string> = asyncFunctions.get(0)("text");
const voidCalled: undefined = callbackLists.void_list().get(0)("text");
// @ts-expect-error callback elements must be functions
functions.append("text");
// @ts-expect-error callback results are typed
functions.set(0, text => 42);
// @ts-expect-error async callbacks must return a PromiseLike
asyncFunctions.append(text => text);
// @ts-expect-error sync callbacks cannot return Promises
functions.append(async text => text);
// @ts-expect-error None callbacks must not return Promises
callbackLists.void_list().append(async text => {});
import {type SnekLibrary as ClassLibrary, type SnekObject} from "./generated/classes.js";
declare const classes: ClassLibrary;
const base = classes.make(1), derived = classes.derived(2);
const asBase: SnekObject<"classes.Counter"> = derived;
const asDerived: ReturnType<typeof classes.derived> = classes.grand(3);
classes.read(classes.grand(3));
const classList = classes.list_base(base);
classList.append(derived);
const classResult: SnekObject<"classes.Counter"> = classes.apply((a, b) => b, base, derived);
const classAsync: Promise<SnekObject<"classes.Counter">> = classes.apply_async(async x => x, derived);
classes.apply_optional(x => x, null);
// @ts-expect-error lists remain invariant even when their class elements support upcasts
classes.apply_rows(x => x, classes.list_derived());
// @ts-expect-error callback results must be compatible class handles
classes.apply(() => classes.other(1), base, derived);
// @ts-expect-error derived lists cannot accept base objects
classes.list_derived().append(base);
classes.read(derived);
classes.nullable(null);
const optionalObject: Promise<SnekObject<"classes.Counter"> | null> = classes.optional_async(null);
// @ts-expect-error base handles cannot be implicitly downcast
classes.require_derived(base);
// @ts-expect-error unrelated classes remain distinct
classes.read(classes.other(1));
// @ts-expect-error plain objects cannot impersonate a handle
classes.read({});
// @ts-expect-error nullable handles need narrowing
classes.read(classes.nullable(base));
// @ts-expect-error fields remain private to Snek
derived.value;
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
        source = self.root / "optional.py"
        source.write_text(OPTIONAL_SOURCE)
        driver.build(source, self.root / "src/generated/optional.wasm", optimize=optimize,
                     compiler=self.compiler, library=True, exports=OPTIONAL_EXPORTS)
        source = self.root / "tuples.py"
        source.write_text(TUPLE_SOURCE)
        driver.build(source, self.root / "src/generated/tuples.wasm", optimize=optimize,
                     compiler=self.compiler, library=True, exports=TUPLE_EXPORTS)
        source = self.root / "dicts.py"
        source.write_text(DICT_SOURCE)
        driver.build(source, self.root / "src/generated/dicts.wasm", optimize=optimize,
                     compiler=self.compiler, library=True, exports=DICT_EXPORTS)
        source = self.root / "higher_callbacks.py"
        source.write_text(HIGHER_CALLBACK_SOURCE)
        driver.build(source, self.root / "src/generated/higher-callbacks.wasm", optimize=optimize,
                     compiler=self.compiler, library=True, exports=HIGHER_CALLBACK_EXPORTS)
        source = self.root / "callback_lists.py"
        source.write_text(CALLBACK_LIST_SOURCE)
        driver.build(source, self.root / "src/generated/callback-lists.wasm", optimize=optimize,
                     compiler=self.compiler, library=True, exports=CALLBACK_LIST_EXPORTS)
        source = self.root / "classes.py"
        source.write_text(CLASS_VALUE_SOURCE)
        driver.build(source, self.root / "src/generated/classes.wasm", optimize=optimize,
                     compiler=self.compiler, library=True, exports=CLASS_VALUE_EXPORTS)
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
