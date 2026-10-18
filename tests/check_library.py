"""Execute generated host bindings against both compilers and the native CLI."""
from contextlib import ExitStack
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from snekc import driver

NATIVE = os.environ.get("SNEK_LIBRARY_NATIVE")
OPTIMIZE = "--optimize" in sys.argv
if OPTIMIZE:
    sys.argv.remove("--optimize")
if "--native" in sys.argv:
    sys.argv.remove("--native")
    NATIVE = str(ROOT / "native/target/release" / ("snek.exe" if os.name == "nt" else "snek"))

SOURCE = '''from snek import i32, i64, f32, f64
print("initialized")
def add(a: i32, b: i32) -> i32:
    return a + b
def big(a: i64) -> i64:
    return a
def single(a: f32) -> f32:
    return a
def double(a: f64) -> f64:
    return a
def flip(a: bool) -> bool:
    return not a
def text(a: str, b: str) -> str:
    return a + b
def count(a: str) -> i32:
    return len(a)
def show(a: str) -> None:
    print(a)
def fail() -> i32:
    raise ValueError("bad 🐍")
def internal() -> i32:
    return 99
'''
EXPORTS = ["add", "big", "single", "double", "flip", "text", "count", "show", "fail"]

LIST_SOURCE = '''from snek import i32, HostObject, host, checkpoint
@host("edit")
def edit(items: list[str], before: str, after: str) -> list[str]:
    pass
@host("later")
async def later(items: list[str]) -> list[str]:
    pass
def words() -> list[str]:
    return ["first", "last"]
def numbers() -> list[i32]:
    return [1, 2]
def echo(items: list[str]) -> list[str]:
    return items
def upper(items: list[str]) -> str:
    return items[-1].upper()
def mutate(items: list[str]) -> list[str]:
    items.append("snek")
    return items
def from_host(items: list[str]) -> list[str]:
    print("before")
    result = edit(items, "left", "right")
    print("after")
    return result
async def delayed(items: list[str]) -> list[str]:
    await checkpoint()
    result = await later(items)
    result.append("awaited")
    return result
def objects(value: HostObject) -> list[HostObject]:
    return [value]
def nullable(value: HostObject | None) -> list[HostObject | None]:
    return [value]
'''
LIST_EXPORTS = ["words", "numbers", "echo", "upper", "mutate", "from_host", "delayed", "objects", "nullable"]

NESTED_LIST_SOURCE = '''from snek import HostObject, host, checkpoint, gather
@host("edit")
def edit(rows: list[list[str]]) -> list[list[str]]:
    pass
@host("later")
async def later(rows: list[list[str]]) -> list[list[str]]:
    pass
def rows() -> list[list[str]]:
    shared = ["one", "two"]
    return [shared, shared]
def echo(rows: list[list[str]]) -> list[list[str]]:
    return rows
def child(row: list[str]) -> list[str]:
    return row
def upper(rows: list[list[str]]) -> str:
    return rows[0][-1].upper()
def from_host(rows: list[list[str]]) -> list[list[str]]:
    return edit(rows)
async def delayed(rows: list[list[str]]) -> list[list[str]]:
    return await later(rows)
async def row(text: str) -> list[str]:
    await checkpoint()
    return [text]
async def collected() -> list[list[str]]:
    return await gather(row("first"), row("second"))
def cube() -> list[list[list[str]]]:
    return [rows()]
def nullable(value: HostObject | None) -> list[list[HostObject | None]]:
    return [[value]]
'''
NESTED_LIST_EXPORTS = ["rows", "echo", "child", "upper", "from_host", "delayed", "collected", "cube", "nullable"]

LIST_CALLBACK_SOURCE = '''from snek import host, checkpoint
from typing import Callable, Coroutine
@host("install")
def install(fn: Callable[[list[str]], list[str]]) -> Callable[[list[str]], list[str]]:
    pass
@host("install_async")
async def install_async(fn: Callable[[list[list[str]]], Coroutine[None, None, list[list[str]]]]) -> Callable[[list[list[str]]], Coroutine[None, None, list[list[str]]]]:
    pass
def words() -> list[str]:
    return ["word"]
def rows() -> list[list[str]]:
    return [["row"]]
def make(text: str) -> Callable[[list[str]], list[str]]:
    def append(items: list[str]) -> list[str]:
        items.append(text)
        return items
    return append
def make_async(text: str) -> Callable[[list[list[str]]], Coroutine[None, None, list[list[str]]]]:
    async def append(items: list[list[str]]) -> list[list[str]]:
        await checkpoint()
        items[0].append(text)
        return items
    return append
def apply(fn: Callable[[list[str]], list[str]], items: list[str]) -> list[str]:
    return fn(items)
async def apply_async(fn: Callable[[list[list[str]]], Coroutine[None, None, list[list[str]]]], items: list[list[str]]) -> list[list[str]]:
    return await fn(items)
def roundtrip(fn: Callable[[list[str]], list[str]]) -> Callable[[list[str]], list[str]]:
    return install(fn)
async def roundtrip_async(fn: Callable[[list[list[str]]], Coroutine[None, None, list[list[str]]]]) -> Callable[[list[list[str]]], Coroutine[None, None, list[list[str]]]]:
    return await install_async(fn)
def factory() -> Callable[[], list[list[str]]]:
    def create() -> list[list[str]]:
        return [["created"]]
    return create
def visit(fn: Callable[[list[list[str]]], list[list[str]]]) -> str:
    return fn([["visit"]])[0][0]
def combine(fn: Callable[[list[str], str, list[str]], list[str]], items: list[str]) -> list[str]:
    return fn(items, "between", items)
async def combine_async(fn: Callable[[list[list[str]], str], Coroutine[None, None, list[list[str]]]], items: list[list[str]]) -> list[list[str]]:
    return await fn(items, "between async")
'''
LIST_CALLBACK_EXPORTS = ["words", "rows", "make", "make_async", "apply", "apply_async", "roundtrip", "roundtrip_async", "factory", "visit", "combine", "combine_async"]

OBJECT_SOURCE = '''from snek import HostObject, host, checkpoint
from typing import Callable, Iterator
@host("lookup")
def lookup(name: str) -> HostObject | None:
    pass
@host("touch")
def touch(value: HostObject) -> None:
    pass
@host("resolve")
async def resolve(value: HostObject) -> HostObject:
    pass
@host("wait")
async def wait() -> None:
    pass
class Holder:
    value: HostObject | None
    def __init__(self, value: HostObject | None) -> None:
        self.value = value
def echo(value: HostObject) -> HostObject:
    return value
def optional(value: HostObject | None) -> HostObject | None:
    return Holder(value).value
def same(a: HostObject | None, b: HostObject | None) -> bool:
    return a is b and a == b and not (a != b) and not (a is not b)
def find(name: str) -> HostObject | None:
    value = lookup(name)
    if value is not None:
        touch(value)
    return value
def sequence(value: HostObject) -> Iterator[HostObject]:
    yield value
def collections(a: HostObject, b: HostObject) -> bool:
    values: list[HostObject] = [a, b, a]
    if values.count(a) != 2 or b not in values:
        return False
    values.remove(b)
    mapping: dict[str, HostObject] = {"a": a}
    for value in sequence(a):
        if value is not a:
            return False
    return values == [a, a] and mapping == {"a": a} and (a, b) == (a, b)
def closure(value: HostObject) -> HostObject:
    def get() -> HostObject:
        return value
    callback: Callable[[], HostObject] = get
    return callback()
async def hold(value: HostObject) -> HostObject:
    holder = Holder(value)
    await wait()
    result = holder.value
    if result is None:
        raise ValueError("lost object")
    return await resolve(result)
async def optional_async(value: HostObject | None) -> HostObject | None:
    await checkpoint()
    return value
'''
OBJECT_EXPORTS = ["echo", "optional", "same", "find", "collections", "closure", "hold", "optional_async"]

CALLBACK_SOURCE = '''from snek import host, i32, HostObject, checkpoint
from typing import Callable
@host("store")
def store(callback: Callable[[str], None]) -> None:
    pass
@host("get_callback")
def get_callback() -> Callable[[str], str]:
    pass
@host("get_async_callback")
async def get_async_callback() -> Callable[[str], str]:
    pass
def apply(fn: Callable[[str], str], text: str) -> str:
    return fn(text).upper()
def echo(fn: Callable[[str], str]) -> Callable[[str], str]:
    return fn
def same(a: Callable[[str], str], b: Callable[[str], str]) -> bool:
    return a is b
def make(prefix: str) -> Callable[[str], str]:
    def callback(text: str) -> str:
        return prefix + text.upper()
    return callback
def counter(start: i32) -> Callable[[], i32]:
    def next() -> i32:
        nonlocal start
        start += 1
        return start
    return next
def install(prefix: str) -> None:
    def event(text: str) -> None:
        print(prefix + text)
    store(event)
def from_host(text: str) -> str:
    fn = get_callback()
    return fn(text)
async def from_async_host(text: str) -> str:
    fn = await get_async_callback()
    return fn(text)
async def deferred(fn: Callable[[str], str], text: str) -> str:
    await checkpoint()
    return fn(text)
async def deferred_make(prefix: str) -> Callable[[str], str]:
    await checkpoint()
    return make(prefix)
def apply_void(fn: Callable[[HostObject | None], None], value: HostObject | None) -> None:
    fn(value)
def object_callback() -> Callable[[HostObject | None], HostObject | None]:
    def echo_object(value: HostObject | None) -> HostObject | None:
        return value
    return echo_object
def failer() -> Callable[[], None]:
    def fail() -> None:
        raise ValueError("callback failed")
    return fail
'''
CALLBACK_EXPORTS = ["apply", "echo", "same", "make", "counter", "install", "from_host", "from_async_host", "deferred", "deferred_make", "apply_void", "object_callback", "failer"]

ASYNC_CALLBACK_SOURCE = '''from snek import checkpoint, HostObject
from typing import Callable, Coroutine
async def apply(fn: Callable[[str], Coroutine[None, None, str]], text: str) -> str:
    return (await fn(text)).upper()
def echo(fn: Callable[[str], Coroutine[None, None, str]]) -> Callable[[str], Coroutine[None, None, str]]:
    return fn
def make(prefix: str) -> Callable[[str], Coroutine[None, None, str]]:
    async def callback(text: str) -> str:
        await checkpoint()
        return prefix + text.upper()
    return callback
async def recover(fn: Callable[[str], Coroutine[None, None, str]]) -> str:
    try:
        return await fn("input")
    except RuntimeError as error:
        return str(error)
    finally:
        await checkpoint()
        print("cleaned")
def failer() -> Callable[[str], Coroutine[None, None, str]]:
    async def callback(text: str) -> str:
        await checkpoint()
        raise ValueError(text)
    return callback
async def apply_void(fn: Callable[[], Coroutine[None, None, None]]) -> None:
    await fn()
def object_callback() -> Callable[[HostObject | None], Coroutine[None, None, HostObject | None]]:
    async def callback(value: HostObject | None) -> HostObject | None:
        await checkpoint()
        return value
    return callback
'''
ASYNC_CALLBACK_EXPORTS = ["apply", "echo", "make", "recover", "failer", "apply_void", "object_callback"]

WAIT_ALL_SOURCE = '''from snek import host, checkpoint, wait_all, gather
from typing import Callable, Coroutine
@host("read")
async def read(name: str) -> str:
    pass
@host("cleanup")
async def cleanup(name: str) -> None:
    pass
@host("report")
def report(text: str) -> None:
    pass
async def child(name: str) -> str:
    try:
        value = await read(name)
        report(name + ":" + value)
        return value
    finally:
        await cleanup(name)
        report("done:" + name)
async def pair() -> str:
    await wait_all(child("a"), child("b"))
    return "finished"
async def breaker() -> None:
    await checkpoint()
    await checkpoint()
    raise ValueError("first failure")
async def failfast() -> None:
    await wait_all(child("blocked"), breaker())
async def nested() -> None:
    await wait_all(child("outer"), failfast())
async def duplicate() -> str:
    value = child("reuse")
    try:
        await wait_all(value, value)
    except RuntimeError:
        return await value
    return "unexpected"
async def reused() -> str:
    value = child("used")
    await value
    try:
        await wait_all(value)
    except RuntimeError as error:
        return str(error)
    return "unexpected"
async def invoke(fn: Callable[[str], Coroutine[None, None, str]], name: str) -> None:
    report(await fn(name))
async def callbacks(fn: Callable[[str], Coroutine[None, None, str]]) -> None:
    await wait_all(invoke(fn, "one"), invoke(fn, "two"))
async def gathered() -> str:
    values = await gather(child("a"), child("b"))
    return "|".join(values)
async def fail_text() -> str:
    await checkpoint()
    raise ValueError("gather failure")
async def gather_failure() -> None:
    await gather(child("blocked"), fail_text())
'''
WAIT_ALL_EXPORTS = ["pair", "failfast", "nested", "duplicate", "reused", "callbacks", "gathered", "gather_failure"]

HOST_SOURCE = '''from snek import host, i32, i64, f32, f64
@host("render")
def render(value: str) -> None:
    """Let the application decide where to display this text."""
    pass
@host("transform")
def transform(a: str, b: str) -> str:
    pass
@host("numbers")
def numbers(a: i32, b: i64, c: f32, d: f64, e: bool) -> i64:
    pass
@host("get_text")
def get_text() -> str:
    pass
@host("integer")
def integer() -> i32:
    pass
@host("boolean")
def boolean() -> bool:
    pass
@host("single")
def single() -> f32:
    pass
@host("double")
def double() -> f64:
    pass
render("ready 🐍")
def upper() -> str:
    return get_text().upper()
def repeat(n: i32) -> i32:
    total = 0
    for i in range(n):
        total += len(transform("a", "b"))
    return total
def guarded() -> str:
    try:
        return get_text()
    except Exception:
        return "caught"
'''
HOST_EXPORTS = ["render", "transform", "numbers", "upper", "repeat", "integer", "boolean", "single", "double", "guarded"]

ASYNC_SOURCE = '''from snek import host, i32, i64, f32, f64, checkpoint, run
@host("read_text")
async def read_text(value: str) -> str:
    pass
@host("wait")
async def wait() -> None:
    pass
@host("number")
async def number(value: i64) -> i64:
    pass
@host("scalar")
async def scalar(a: i32, b: f32, c: f64, d: bool) -> f64:
    pass
@host("render")
def render(text: str) -> None:
    pass
async def load(value: str) -> str:
    try:
        return (await read_text(value)).upper()
    finally:
        render("done")
async def recover() -> str:
    try:
        return await read_text("recover")
    except RuntimeError as error:
        return str(error)
    finally:
        await wait()
        render("cleaned")
async def flow(value: str) -> str:
    first = await load(value)
    await checkpoint()
    await wait()
    return first + await read_text("!")
async def repeat(n: i32) -> i32:
    total = 0
    for i in range(n):
        total += len(await read_text("x"))
    return total
async def plain(value: i32) -> i32:
    return value + 1
async def failure() -> None:
    raise ValueError("async failure")
def sync(value: i32) -> i32:
    return value
'''
ASYNC_EXPORTS = ["read_text", "wait", "number", "scalar", "load", "recover", "flow", "repeat", "plain", "failure", "sync"]


class LibraryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="snek-library-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.src = self.root / "entry.py"
        self.src.write_text(SOURCE)
        self.compiler = os.environ.get("SNEK_COMPILER", "python")
        self.native = NATIVE
        # Explicit ES-module scope also exercises Node with a package manifest.
        (self.root / "package.json").write_text('{"type":"module"}')

    def build(self, exports=EXPORTS, **kwargs):
        out = self.root / kwargs.pop("output", "output.wasm")
        if self.native:
            command = [self.native, "build", str(self.src), "--library", "-o", str(out)]
            for name in exports: command += ["--export", name]
            result = subprocess.run(command, capture_output=True, text=True, timeout=60)
            if result.returncode: raise RuntimeError(result.stderr)
            return driver.Result(None, out, js_path=out.with_suffix(".js"), dts_path=out.with_suffix(".d.ts"))
        return driver.build(self.src, out, optimize=OPTIMIZE, compiler=self.compiler,
                            library=True, exports=exports, **kwargs)

    def node(self, script):
        result = subprocess.run([shutil.which("node") or "node", "--input-type=module", "-e",
            'import assert from "node:assert/strict";\n'
            'import {readFile} from "node:fs/promises";\n'
            'import {instantiate, SnekError} from "./output.js";\n'
            'const bytes = await readFile("./output.wasm");\n' + script],
            cwd=self.root, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_types_strings_errors_and_lifetime(self):
        result = self.build()
        self.assertIn('"big"(arg0: bigint): bigint;', result.dts_path.read_text())
        self.assertIn('"show"(arg0: string): void;', result.dts_path.read_text())
        self.node('''
let output = "";
const lib = await instantiate(bytes, {stdout: text => output += text});
assert.equal(output, "initialized\\n");
assert.equal(lib.add(20, 22), 42);
assert.equal(lib.add(2147483647, 1), -2147483648);
assert.equal(lib.big(9007199254740993n), 9007199254740993n);
assert.equal(lib.big(-(1n << 63n)), -(1n << 63n));
assert.equal(lib.big((1n << 63n) - 1n), (1n << 63n) - 1n);
assert.equal(lib.single(1.1), Math.fround(1.1));
assert.equal(lib.double(Infinity), Infinity);
assert.ok(Number.isNaN(lib.double(NaN)));
assert.ok(Object.is(lib.double(-0), -0));
assert.equal(lib.flip(true), false);
assert.equal(lib.flip(false), true);
for (const value of ["", "ASCII", "\\0", "🐍é中", "\\ufeffbom", "🐍".repeat(100000)]) {
  assert.equal(lib.text(value, value), value + value);
  assert.equal(lib.count(value), [...value].length);
}
for (let i = 0; i < 300; i++) assert.equal(lib.text("a".repeat(10000), "b").length, 10001);
assert.equal(lib.show("hello 🐍"), undefined);
assert.equal(output, "initialized\\nhello 🐍\\n");
assert.throws(() => lib.fail(), e => e instanceof SnekError && e.message === "ValueError: bad 🐍");
assert.equal(lib.add(1, 2), 3);
assert.equal(lib.internal, undefined);
assert.equal(lib.__snek_init, undefined);
assert.ok(Object.isFrozen(lib));
const second = await instantiate(await WebAssembly.compile(bytes));
assert.equal(second.add(2, 3), 5);
''')

    def test_list_callback_values_and_identity(self):
        self.src.write_text(LIST_CALLBACK_SOURCE)
        self.build(LIST_CALLBACK_EXPORTS)
        self.node('''
const options = {imports: {install: fn => fn, install_async: async fn => fn}};
const lib = await instantiate(bytes, options);
const words = lib.words();
assert.equal(lib.combine((a, text, b) => {assert.equal(a, b); assert.equal(text, "between"); return a;}, words), words);
const append = lib.make("🐍");
assert.equal(append(words), words);
assert.deepEqual(words.toArray(), ["word", "🐍"]);
assert.equal(lib.roundtrip(append), append);
const js = items => {items.append("JS"); return items;};
assert.equal(lib.roundtrip(js), js);
assert.equal(lib.apply(js, words), words);
assert.equal(words.get(-1), "JS");
assert.throws(() => append(["array"]), /handle from this Snek instance/);
assert.throws(() => lib.apply(() => [], words), /handle from this Snek instance/);
const other = await instantiate(bytes, options);
assert.throws(() => append(other.words()), /handle from this Snek instance/);
assert.throws(() => lib.apply(() => other.words(), words), /handle from this Snek instance/);
assert.throws(() => lib.apply(async items => items, words), /must be synchronous/);
assert.equal(append(words), words);
const rows = lib.rows(), asyncAppend = lib.make_async("awaited");
assert.equal(await lib.combine_async(async (a, text) => {assert.equal(text, "between async"); return a;}, rows), rows);
assert.equal(await asyncAppend(rows), rows);
assert.equal(await lib.roundtrip_async(asyncAppend), asyncAppend);
const asyncJS = async items => {items.get(0).append("async JS"); return items;};
assert.equal(await lib.roundtrip_async(asyncJS), asyncJS);
assert.equal(await lib.apply_async(asyncJS, rows), rows);
assert.deepEqual(rows.get(0).toArray(), ["row", "awaited", "async JS"]);
await assert.rejects(lib.apply_async(async () => [], rows), SnekError);
await assert.rejects(lib.apply_async(async () => other.rows(), rows), SnekError);
await assert.rejects(asyncAppend(other.rows()), /handle from this Snek instance/);
const [a, b] = await Promise.all([asyncAppend(rows), asyncAppend(rows)]);
assert.equal(a, rows); assert.equal(b, rows);
''')

    def test_list_callback_element_types(self):
        source = 'from snek import i32, i64, f32, f64, HostObject\nfrom typing import Callable\n'
        exports = []
        for i, elem in enumerate(("i32", "i64", "f32", "f64", "bool", "HostObject", "HostObject | None")):
            source += f'def make{i}(value: {elem}) -> list[{elem}]:\n    return [value]\n'
            source += f'def apply{i}(fn: Callable[[list[{elem}], list[{elem}]], list[{elem}]], items: list[{elem}]) -> list[{elem}]:\n    return fn(items, items)\n'
            exports += [f"make{i}", f"apply{i}"]
        self.src.write_text(source)
        self.build(exports)
        self.node('''
const lib = await instantiate(bytes);
const values = [42, 1n << 55n, 1.25, -Infinity, true, {}, null];
for (const [i, value] of values.entries()) {
  const list = lib[`make${i}`](value);
  assert.equal(lib[`apply${i}`]((a, b) => {
    assert.equal(a, b); assert.equal(a.get(0), value); a.append(value); return b;
  }, list), list);
  assert.deepEqual(list.toArray(), [value, value]);
}
''')

    def test_lists_only_reachable_through_callback_signatures(self):
        self.src.write_text(LIST_CALLBACK_SOURCE)
        self.build(["factory", "visit"])
        self.node('''
const lib = await instantiate(bytes, {imports: {install: fn => fn, install_async: async fn => fn}});
const rows = lib.factory()();
assert.deepEqual(rows.get(0).toArray(), ["created"]);
let received;
assert.equal(lib.visit(items => {received = items; items.get(0).set(0, "changed"); return items;}), "changed");
assert.deepEqual(received.toArray().map(row => row.toArray()), [["changed"]]);
''')

    def test_nested_list_handles_preserve_aliases_and_types(self):
        self.src.write_text(NESTED_LIST_SOURCE)
        built = self.build(NESTED_LIST_EXPORTS)
        self.assertIn('"cube"(): SnekList<SnekList<SnekList<string>>>', built.dts_path.read_text())
        self.node('''
const options = {imports: {edit: rows => rows, later: async rows => rows}};
const lib = await instantiate(bytes, options);
const rows = lib.rows(), shared = rows.get(0);
assert.equal(rows.get(1), shared);
assert.equal(lib.child(shared), shared);
assert.equal(lib.echo(rows), rows);
assert.deepEqual(rows.toArray(), [shared, shared]); // shallow, live child handles
shared.append("straße 🐍");
assert.equal(rows.get(1).get(-1), "straße 🐍");
assert.equal(lib.upper(rows), "STRASSE 🐍");
rows.append(shared); rows.set(0, shared);
assert.equal(rows.pop(), shared);
assert.throws(() => rows.append(["array"]), /handle from this Snek instance/);
assert.throws(() => rows.append(rows), /handle from this Snek instance/);
const other = await instantiate(bytes, options);
assert.throws(() => rows.set(0, other.rows().get(0)), /handle from this Snek instance/);
assert.equal(rows.get(0), shared);
const cube = lib.cube();
cube.append(rows);
assert.equal(cube.pop(), rows);
assert.equal(cube.get(0).get(0), cube.get(0).get(1));
const nullable = lib.nullable(null), object = {};
nullable.get(0).append(object);
assert.deepEqual(nullable.get(0).toArray(), [null, object]);
assert.throws(() => rows.append(nullable.get(0)), /handle from this Snek instance/);
rows.pop(); rows.pop();
assert.equal(rows.length, 0);
assert.equal(shared.get(-1), "straße 🐍"); // child outlives parent membership
''')

    def test_nested_list_host_calls_and_gather(self):
        self.src.write_text(NESTED_LIST_SOURCE)
        self.build(NESTED_LIST_EXPORTS)
        self.node('''
let saved, release;
const lib = await instantiate(bytes, {imports: {
  edit(rows) {
    assert.equal(rows, saved);
    rows.get(0).append("🐍".repeat(40000));
    rows.append(rows.get(0));
    assert.equal(rows.pop(), rows.get(1));
    return rows;
  },
  later: rows => new Promise(resolve => {release = () => resolve(rows);}),
}});
saved = lib.rows();
assert.equal(lib.from_host(saved), saved);
assert.equal(saved.get(1).get(-1).length, 80000);
const pending = lib.delayed(saved);
while (!release) await new Promise(resolve => setTimeout(resolve, 1));
saved.get(0).set(0, "changed"); release();
assert.equal(await pending, saved);
assert.equal(saved.get(1).get(0), "changed");
const collected = await lib.collected();
assert.deepEqual(collected.toArray().map(row => row.toArray()), [["first"], ["second"]]);
''')

    def test_typed_list_handles_identity_mutation_and_lifetime(self):
        self.src.write_text(LIST_SOURCE)
        built = self.build(LIST_EXPORTS)
        self.assertIn('"words"(): SnekList<string>', built.dts_path.read_text())
        self.node('''
const options = {imports: {edit: x => x, later: async x => x}};
const lib = await instantiate(bytes, options);
const list = lib.words();
assert.deepEqual(list.toArray(), ["first", "last"]);
assert.equal(list.length, 2);
assert.equal(list.get(-1), "last");
assert.equal(lib.echo(list), list);
assert.equal(lib.mutate(list), list);
list.set(-1, "straße 🐍");
assert.equal(lib.upper(list), "STRASSE 🐍");
assert.equal(list.pop(), "straße 🐍");
for (let i = 0; i < 100; i++) list.append(String(i));
assert.equal(list.length, 102);
const snapshot = list.toArray(); snapshot[0] = "copy";
assert.equal(list.get(0), "first");
for (const i of [-103, 102, 2**32]) assert.throws(() => list.get(i), RangeError);
for (const i of [NaN, Infinity, 0.5, "0", 1n]) assert.throws(() => list.set(i, "x"), TypeError);
assert.throws(() => list.set(0, 12), /expected string/);
assert.throws(() => list.append("\\ud800"), /surrogate/);
assert.equal(list.get(0), "first");
assert.throws(() => lib.echo(["array"]), /handle from this Snek instance/);
assert.throws(() => lib.echo({...list}), /handle from this Snek instance/);
const other = await instantiate(bytes, options);
assert.throws(() => lib.echo(other.words()), /handle from this Snek instance/);
assert.throws(() => lib.echo(lib.numbers()), /handle from this Snek instance/);
const value = {};
assert.equal(lib.objects(value).get(0), value);
const optional = lib.nullable(null);
optional.append(value); optional.set(0, value);
assert.deepEqual(optional.toArray(), [value, value]);
optional.set(0, null);
assert.equal(optional.pop(), value);
assert.equal(optional.pop(), null);
assert.throws(() => optional.pop(), RangeError);
assert.throws(() => optional.get(0), RangeError);
assert.throws(() => optional.append(undefined), /HostObject/);
assert(Object.isFrozen(list));
// The handle itself owns the GC reference, independently of the public API.
const survivor = (await instantiate(bytes, options)).words();
survivor.append("alive"); assert.equal(survivor.pop(), "alive");
''')

    def test_list_handles_inside_host_calls_and_async_steps(self):
        self.src.write_text(LIST_SOURCE)
        self.build(LIST_EXPORTS)
        self.node('''
let lib, saved, output = "", release;
lib = await instantiate(bytes, {stdout: text => {output += text;}, imports: {
  edit(items, before, after) {
    assert.equal(before, "left"); assert.equal(after, "right");
    assert.equal(items, saved);
    items.set(0, "🐍".repeat(40000));
    assert.equal(items.get(0).length, 80000);
    assert.throws(() => items.set(999, "scratch"), RangeError);
    items.append("résumé");
    assert.equal(items.pop(), "résumé");
    assert.throws(() => lib.words(), /not reentrant/);
    return items;
  },
  later: items => new Promise(resolve => {release = () => resolve(items);}),
}});
saved = lib.words();
assert.equal(lib.from_host(saved), saved);
assert.equal(output, "before\\nafter\\n");
assert.equal(saved.get(0), "🐍".repeat(40000));
const pending = lib.delayed(saved);
while (!release) await new Promise(resolve => setTimeout(resolve, 1));
saved.set(1, "while waiting"); release();
assert.equal(await pending, saved);
assert.deepEqual(saved.toArray().slice(1), ["while waiting", "awaited"]);
const bad = await instantiate(bytes, {imports: {edit: () => [], later: async () => []}});
assert.throws(() => bad.from_host(bad.words()), /handle from this Snek instance/);
await assert.rejects(bad.delayed(bad.words()), SnekError);
''')

    def test_list_handles_from_stdout_can_grow_memory(self):
        self.src.write_text(LIST_SOURCE)
        self.build(LIST_EXPORTS)
        self.node('''
let saved, output = "";
const lib = await instantiate(bytes, {imports: {edit: x => x, later: async x => x},
  stdout(text) {output += text; saved.append("x".repeat(500000));},
});
saved = lib.words();
assert.equal(lib.from_host(saved), saved);
assert.equal(output, "before\\nafter\\n");
assert.equal(saved.get(-1).length, 500000);
assert.equal(lib.echo(saved), saved);
''')

    def test_list_host_only_signatures_and_initialization(self):
        self.src.write_text('''from snek import host, i64
@host("take")
def take(items: list[i64]) -> list[i64]:
    pass
def total() -> i64:
    return sum(take([1, 2]))
print(total())
''')
        self.build(["total"])
        self.node('''
let output = "", saved;
const lib = await instantiate(bytes, {stdout: text => {output += text;}, imports: {
  take(items) {saved = items; items.append(3n); return items;},
}});
assert.equal(output, "6\\n");
assert.deepEqual(saved.toArray(), [1n, 2n, 3n]);
assert.equal(lib.total(), 6n);
''')

    def test_list_scalar_element_types(self):
        self.src.write_text('from snek import i32, i64, f32, f64\n' + '\n'.join(
            f'def {ty}_list() -> list[{ty}]:\n    return []\n' for ty in ("i32", "i64", "f32", "f64", "bool")))
        self.build([ty + "_list" for ty in ("i32", "i64", "f32", "f64", "bool")])
        self.node('''
const lib = await instantiate(bytes);
for (const [type, value] of [["i32", -2147483648], ["i64", -(1n<<63n)], ["f32", 1.25], ["f64", Infinity], ["bool", true]]) {
  const list = lib[type + "_list"]();
  list.append(value); list.set(0, value);
  assert.deepEqual(list.toArray(), [value]);
  assert.equal(list.pop(), value); assert.equal(list.length, 0);
}
assert.throws(() => lib.i32_list().append(2**31), RangeError);
assert.throws(() => lib.i64_list().append(1), TypeError);
assert.throws(() => lib.bool_list().append(1), TypeError);
''')

    def test_opaque_host_objects(self):
        self.src.write_text(OBJECT_SOURCE)
        result = self.build(OBJECT_EXPORTS)
        self.assertIn('"echo"(arg0: object): object;', result.dts_path.read_text())
        self.assertIn('"optional_async"(arg0: object | null, options?: SnekCallOptions): Promise<object | null>;', result.dts_path.read_text())
        self.node('''
const a = {name: "a"}, b = {name: "b"};
let touched, release, result = a;
const lib = await instantiate(bytes, {imports: {
  lookup: name => name === "missing" ? null : result,
  touch: value => { touched = value; },
  resolve: async value => value,
  wait: () => new Promise(resolve => {release = resolve;})
}});
for (const value of [a, [], new Date(), () => 42, Object.create(null)]) {
  assert.equal(lib.echo(value), value);
  assert.equal(lib.optional(value), value);
  assert.equal(lib.closure(value), value);
  assert.equal(await lib.optional_async(value), value);
}
assert.equal(lib.optional(null), null);
assert.equal(await lib.optional_async(null), null);
assert.equal(lib.same(a, a), true);
assert.equal(lib.same(a, b), false);
assert.equal(lib.same(a, null), false);
assert.equal(lib.same(null, null), true);
assert.equal(lib.collections(a, b), true);
assert.equal(lib.find("a"), a); assert.equal(touched, a);
assert.equal(lib.find("missing"), null);
const pending = lib.hold(a);
await new Promise(resolve => setTimeout(resolve, 0));
assert.equal(typeof release, "function");
release();
assert.equal(await pending, a);
for (const value of [null, undefined, 1, "text", true, 1n, Symbol(), Promise.resolve(a), {then() {}}])
  assert.throws(() => lib.echo(value), /HostObject/);
for (result of [undefined, 42]) assert.throws(() => lib.find("bad"), /HostObject/);
result = a; assert.equal(lib.find("ok"), a);
''')

    def test_host_objects_remain_opaque(self):
        from snekc.errors import CompileError
        bad = [
            'def bad(value: HostObject) -> str:\n    return value.name\n',
            'def bad(value: HostObject) -> str:\n    return str(value)\n',
            'def bad(value: HostObject) -> bool:\n    return value is "text"\n',
            'def bad(value: HostObject) -> bool:\n    return "text" is value\n',
            'def bad(value: HostObject) -> bool:\n    return value < value\n',
            'def bad(value: HostObject) -> HostObject:\n    return HostObject()\n',
            'def bad(value: HostObject) -> bool:\n    mapping: dict[HostObject, str] = {value: "x"}\n    return True\n',
        ]
        for source in bad:
            with self.subTest(source=source):
                self.src.write_text("from snek import HostObject\n" + source)
                with self.assertRaises((CompileError, ValueError, RuntimeError, driver.ToolError)):
                    self.build(["bad"])

    def test_typed_callbacks_cross_both_directions(self):
        self.src.write_text(CALLBACK_SOURCE)
        result = self.build(CALLBACK_EXPORTS)
        self.assertIn('((arg0: string) => string)', result.dts_path.read_text())
        self.assertIn('((arg0: string) => undefined)', result.dts_path.read_text())
        self.node('''
let saved, output = "", mode = "ok";
const supplied = value => value + " 🐍";
const lib = await instantiate(bytes, {stdout: text => output += text, imports: {
  store: fn => { saved = fn; if (mode === "reenter") fn("immediate"); },
  get_callback: () => supplied,
  get_async_callback: async () => supplied
}});
assert.equal(lib.apply(value => value + "Straße", "hi "), "HI STRASSE");
assert.equal(lib.echo(supplied), supplied);
assert.equal(lib.same(supplied, supplied), true);
assert.equal(lib.same(supplied, value => value), false);
const made = lib.make("prefix: ");
assert.equal(made("Straße 🐍"), "prefix: STRASSE 🐍");
assert.equal(lib.echo(made), made);
assert.equal(lib.apply(made, "Straße"), "PREFIX: STRASSE");
const count = lib.counter(40);
assert.equal(count(), 41); assert.equal(count(), 42);
lib.install("event: "); saved("later 🐍");
assert.equal(output, "event: later 🐍\\n");
assert.equal(lib.from_host("host"), "host 🐍");
assert.equal(await lib.from_async_host("async"), "async 🐍");
assert.equal(await lib.deferred(supplied, "held"), "held 🐍");
assert.equal((await lib.deferred_make("async: "))("Straße"), "async: STRASSE");
let seen;
const object = {};
lib.apply_void(value => {seen = value;}, object);
assert.equal(seen, object);
assert.equal(lib.object_callback()(object), object);
assert.equal(lib.object_callback()(null), null);
assert.throws(() => lib.failer()(), error => error instanceof SnekError && error.message.includes("callback failed"));
assert.throws(() => lib.apply(null, "x"), /expected function/);
assert.throws(() => made(), /expects 1/);
assert.throws(() => made(42), /expected string/);
assert.throws(() => lib.apply(() => 42, "x"), /expected string/);
assert.throws(() => lib.apply(async () => "x", "x"), /must be synchronous/);
assert.throws(() => lib.apply_void(async () => {}, null), /must be synchronous/);
const failure = new Error("JS callback failure");
assert.throws(() => lib.apply(() => {throw failure;}, "x"), error => error === failure);
mode = "reenter";
assert.throws(() => lib.install("bad"), /not reentrant/);
mode = "ok";
assert.equal(made("still works"), "prefix: STILL WORKS");
''')

    def test_async_callback_boundaries(self):
        self.src.write_text(ASYNC_CALLBACK_SOURCE)
        result = self.build(ASYNC_CALLBACK_EXPORTS)
        self.assertIn("=> PromiseLike<string>", result.dts_path.read_text())
        self.node('''
let output = "";
const lib = await instantiate(bytes, {stdout: text => output += text});
const supplied = async text => text + "🐍";
assert.equal(await lib.apply(supplied, "Straße"), "STRASSE🐍");
assert.equal(lib.echo(supplied), supplied);
const made = lib.make("prefix: ");
assert.equal(lib.echo(made), made);
assert.equal(await made("Straße"), "prefix: STRASSE");
assert.equal(await lib.apply(made, "Straße"), "PREFIX: STRASSE");
assert.deepEqual(await Promise.all([made("one"), made("two"), lib.apply(supplied, "three")]),
  ["prefix: ONE", "prefix: TWO", "THREE🐍"]);
assert.equal(await lib.apply(text => ({then(resolve) {resolve(text);}}), "ok"), "OK");
for (const [fn, message] of [[async () => {throw new Error("rejected 🐍");}, "rejected 🐍"],
  [() => {throw new Error("thrown");}, "thrown"], [async () => 42, "expected string"],
  [() => "bare", "must return a Promise"], [async () => "\\ud800", "surrogate"]]) {
  assert.ok((await lib.recover(fn)).includes(message));
  await assert.rejects(lib.apply(fn, "input"), error => error instanceof SnekError && error.message.includes(message));
}
assert.equal(output, "cleaned\\n".repeat(5));
await assert.rejects(lib.failer()("async closure failure"), error => error instanceof SnekError && error.message.includes("async closure failure"));
await assert.rejects(made(42), /expected string/);
await assert.rejects(made(), /expects 1/);
await assert.rejects(lib.apply_void(async () => 42), /must return undefined/);
await lib.apply_void(async () => {});
const object = {};
assert.equal(await lib.object_callback()(object), object);
assert.equal(await lib.object_callback()(null), null);
assert.equal(await made("still works"), "prefix: STILL WORKS");
''')

    def test_async_callback_only_signature_and_host_declarations(self):
        self.src.write_text('''from typing import Callable, Coroutine
def echo(fn: Callable[[str], Coroutine[None, None, str]]) -> Callable[[str], Coroutine[None, None, str]]:
    return fn
''')
        self.build(["echo"])
        self.node('const lib = await instantiate(bytes); const fn = async text => text; assert.equal(lib.echo(fn), fn);')
        self.src.write_text('''from typing import Callable, Coroutine
from snek import host
@host("store")
def store(fn: Callable[[str], Coroutine[None, None, str]]) -> None:
    pass
@host("get")
def get() -> Callable[[str], Coroutine[None, None, str]]:
    pass
@host("get_async")
async def get_async() -> Callable[[str], Coroutine[None, None, str]]:
    pass
async def callback_0(text: str) -> str:
    return text.upper()
def install() -> None:
    store(callback_0)
async def call(text: str) -> str:
    fn = await get_async()
    return await fn(text)
''')
        self.build(["install", "get", "call", "callback_0"])
        self.node('''
let saved;
const supplied = async text => text + "🐍";
const lib = await instantiate(bytes, {imports: {store: fn => {saved = fn;}, get: () => supplied, get_async: async () => supplied}});
lib.install();
assert.equal(await saved("Straße"), "STRASSE");
assert.equal(lib.get(), supplied);
assert.equal(await lib.call("hello"), "hello🐍");
assert.equal(await lib.callback_0("ok"), "OK");
''')

    def test_callback_scalar_signatures(self):
        types = ("i32", "i64", "f32", "f64", "bool")
        self.src.write_text('from snek import i32, i64, f32, f64\nfrom typing import Callable\n' + ''.join(
            f'def echo_{ty}(fn: Callable[[{ty}], {ty}]) -> Callable[[{ty}], {ty}]:\n    return fn\n'
            f'def apply_{ty}(fn: Callable[[{ty}], {ty}], value: {ty}) -> {ty}:\n    return fn(value)\n'
            f'def make_{ty}() -> Callable[[{ty}], {ty}]:\n    def identity(value: {ty}) -> {ty}:\n        return value\n    return identity\n'
            for ty in types))
        self.build([prefix + ty for ty in types for prefix in ("echo_", "apply_", "make_")])
        self.node('''
const lib = await instantiate(bytes);
for (const [type, value] of [["i32", -2147483648], ["i64", 9007199254740993n], ["f32", 1.1], ["f64", -0], ["bool", true]]) {
  const fn = lib[`make_${type}`]();
  const expected = type === "f32" ? Math.fround(value) : value;
  assert.ok(Object.is(fn(value), expected));
  assert.equal(lib[`echo_${type}`](fn), fn);
  assert.ok(Object.is(lib[`apply_${type}`](value => value, value), expected));
}
assert.throws(() => lib.apply_i32(() => 2147483648, 0), RangeError);
assert.throws(() => lib.apply_i64(() => 1, 0n), TypeError);
assert.throws(() => lib.apply_bool(() => 1, true), TypeError);
''')

    def test_async_callback_scalar_signatures(self):
        types = ("i32", "i64", "f32", "f64", "bool")
        self.src.write_text('from snek import i32, i64, f32, f64, checkpoint\nfrom typing import Callable, Coroutine\n' + ''.join(
            f'async def apply_{ty}(fn: Callable[[{ty}], Coroutine[None, None, {ty}]], value: {ty}) -> {ty}:\n    return await fn(value)\n'
            f'def make_{ty}() -> Callable[[{ty}], Coroutine[None, None, {ty}]]:\n    async def identity(value: {ty}) -> {ty}:\n        await checkpoint()\n        return value\n    return identity\n'
            for ty in types))
        self.build([prefix + ty for ty in types for prefix in ("apply_", "make_")])
        self.node('''
const lib = await instantiate(bytes);
await Promise.all([["i32", -2147483648], ["i64", 9007199254740993n], ["f32", 1.1], ["f64", -0], ["bool", true]].map(async ([type, value]) => {
  const fn = lib[`make_${type}`]();
  const expected = type === "f32" ? Math.fround(value) : value;
  assert.ok(Object.is(await fn(value), expected));
  assert.ok(Object.is(await lib[`apply_${type}`](async value => value, value), expected));
}));
await assert.rejects(lib.apply_i32(async () => 2147483648, 0), /i32 out of range/);
await assert.rejects(lib.apply_i64(async () => 1, 0n), /expected bigint/);
await assert.rejects(lib.apply_bool(async () => 1, true), /expected boolean/);
''')

    def test_unsupported_callback_signatures(self):
        for signature in ("Callable[[tuple[str, str]], str]", "Callable[[str], tuple[str, str]]",
                          "Callable[[Callable[[], str]], str]", "Callable[[], Coroutine[str]]",
                          "Callable[[], Coroutine[None, None, tuple[str, str]]]",
                          "Callable[[list[Callable[[], str]]], str]",
                          "Callable[[Coroutine[None, None, str]], str]",
                          "Callable[[None], str]", "Callable[[str], str] | None"):
            for host in (False, True):
                with self.subTest(signature=signature, host=host):
                    self.src.write_text('from snek import host\nfrom typing import Callable, Coroutine\n' +
                        ('@host("f")\n' if host else '') +
                        f'def f(fn: {signature}) -> None:\n    pass\n')
                    with self.assertRaises(Exception):
                        self.build(["f"])

    def test_host_object_modules_and_command_rejection(self):
        from snekc.errors import CompileError
        (self.root / "objects.py").write_text('from snek import HostObject\ndef echo(value: HostObject) -> HostObject:\n    return value\n')
        self.src.write_text('from snek import HostObject\nfrom objects import echo\ndef call(value: HostObject) -> HostObject:\n    return echo(value)\n')
        self.build(["call"])
        self.node('const lib = await instantiate(bytes); const object = {}; assert.equal(lib.call(object), object);')
        command = [self.native] if self.native else [sys.executable, "-m", "snekc"]
        result = subprocess.run([*command, "build", str(self.src)], cwd=ROOT, capture_output=True, timeout=60)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"HostObject requires a --library build", result.stderr)

    def test_argument_validation_and_reentry(self):
        self.build()
        self.node('''
let lib;
lib = await instantiate(bytes, {stdout: () => {
  if (lib) assert.throws(() => lib.add(1, 2), /not reentrant/);
}});
for (const value of [1.5, NaN, Infinity, 2147483648, -2147483649]) assert.throws(() => lib.add(value, 0), RangeError);
assert.throws(() => lib.big(1), TypeError);
assert.throws(() => lib.big(1n << 63n), RangeError);
assert.throws(() => lib.big(-(1n << 63n) - 1n), RangeError);
assert.throws(() => lib.flip(1), TypeError);
assert.throws(() => lib.double("1"), TypeError);
assert.throws(() => lib.text(1, ""), TypeError);
assert.throws(() => lib.text("\\ud800", ""), /surrogate/);
assert.throws(() => lib.text("\\udc00", ""), /surrogate/);
assert.throws(() => lib.add(1), /expects 2/);
assert.throws(() => lib.add(1, 2, 3), /expects 2/);
lib.show("reentry");
assert.equal(lib.text("still", " works"), "still works");
''')

    def test_host_imports_types_strings_initialization_and_arena(self):
        self.src.write_text(HOST_SOURCE)
        result = self.build(exports=HOST_EXPORTS)
        declarations = result.dts_path.read_text()
        self.assertIn('"render"(arg0: string): undefined;', declarations)
        self.assertIn('imports: SnekImports', declarations)
        self.assertIn('options: LibraryOptions', declarations)
        self.node('''
let output = [], lib, memory;
const instantiateWasm = WebAssembly.instantiate;
WebAssembly.instantiate = async (...args) => {
  const instance = await instantiateWasm(...args);
  memory = instance.exports.memory;
  return instance;
};
const imports = {
  render: value => {output.push(value);},
  transform: (a, b) => a + b,
  numbers: (a, b, c, d, e) => {
    assert.deepEqual([a, c, d, e], [-2147483648, Math.fround(1.1), Infinity, true]);
    return b;
  },
  get_text: () => "Straße",
  integer: () => -2147483648,
  boolean: () => true,
  single: () => 1.1,
  double: () => -0,
};
try {lib = await instantiate(bytes, {imports});}
finally {WebAssembly.instantiate = instantiateWasm;}
assert.deepEqual(output, ["ready 🐍"]);
assert.equal(lib.numbers(-2147483648, 9007199254740993n, 1.1, Infinity, true), 9007199254740993n);
assert.equal(lib.upper(), "STRASSE");
assert.equal(lib.integer(), -2147483648);
assert.equal(lib.boolean(), true);
assert.equal(lib.single(), Math.fround(1.1));
assert.ok(Object.is(lib.double(), -0));
const size = memory.buffer.byteLength;
assert.equal(lib.repeat(100000), 200000);
assert.equal(memory.buffer.byteLength, size); // each host call releases its scratch frame
for (const value of ["", "\\0", "\\ufeffbom", "🐍中é", "🐍".repeat(100000)])
  assert.equal(lib.transform(value, value), value + value);
imports.transform = () => "replacement";
assert.equal(lib.transform("a", "b"), "ab"); // callbacks are snapshotted
lib.render("later");
assert.deepEqual(output, ["ready 🐍", "later"]);
''')

    def test_host_import_errors_and_reentry_recover(self):
        self.src.write_text(HOST_SOURCE)
        self.build(exports=HOST_EXPORTS)
        self.node('''
const base = {render: () => {}, transform: (a, b) => a + b,
  numbers: (a, b) => b, get_text: () => "ok", integer: () => 42,
  boolean: () => true, single: () => 1, double: () => 2};
await assert.rejects(instantiate(bytes), /Missing host import function: render/);
await assert.rejects(instantiate(bytes, {imports: Object.create(base)}), /Missing host import/);
await assert.rejects(instantiate(bytes, {imports: {...base, render: 1}}), /Missing host import/);
await assert.rejects(instantiate(bytes, {imports: null}), /imports must be an object/);
const thrown = new Error("host failure");
await assert.rejects(instantiate(bytes, {imports: {...base, render: () => {throw thrown;}}}), e => e === thrown);
let mode = "ok", lib;
lib = await instantiate(bytes, {imports: {...base, get_text: () => {
  if (mode === "throw") throw thrown;
  if (mode === "reenter") return lib.upper();
  if (mode === "promise") return Promise.resolve("later");
  if (mode === "surrogate") return "\\ud800";
  if (mode === "number") return 42;
  return "ok";
}}});
for (const [value, error] of [["throw", e => e === thrown], ["reenter", /not reentrant/],
  ["promise", /must be synchronous/], ["surrogate", /surrogate/], ["number", /expected string/]]) {
  mode = value;
  assert.throws(() => lib.guarded(), error); // JS errors are not Snek exceptions
  mode = "ok";
  assert.equal(lib.upper(), "OK");
}
for (const [name, value, error] of [["integer", 2147483648, RangeError], ["numbers", 1n << 63n, RangeError],
  ["numbers", 42, TypeError], ["boolean", 1, TypeError], ["single", "1", TypeError],
  ["double", null, TypeError]]) {
  const api = await instantiate(bytes, {imports: {...base, [name]: () => value}});
  assert.throws(() => name === "numbers" ? api.numbers(1, 2n, 3, 4, false) : api[name](), error);
}
await assert.rejects(instantiate(bytes, {imports: {...base, render: () => Promise.resolve()}}), /must be synchronous/);
await assert.rejects(instantiate(bytes, {imports: {...base, render: () => Promise.reject(new Error("wrong declaration"))}}), /must be synchronous/);
await assert.rejects(instantiate(bytes, {imports: {...base, render: () => 1}}), /must return undefined/);
''')

    def test_host_imports_across_modules_and_callable_values(self):
        (self.root / "bridge.py").write_text('''from snek import host
@host("__proto__")
def text(a: str) -> str:
    pass
''')
        (self.root / "other.py").write_text('''from snek import host
@host("__proto__")
def another(value: str) -> str:
    pass
''')
        self.src.write_text('''from bridge import text
from other import another
from typing import Callable
def call(value: str) -> str:
    fn: Callable[[str], str] = text
    return another(fn(value))
''')
        self.build(exports=["call"])
        self.node('''
const imports = Object.create(null);
imports.__proto__ = text => text + "🐍";
const lib = await instantiate(bytes, {imports});
assert.equal(lib.call("hi"), "hi🐍🐍");
''')

    def test_host_declarations_reject_unsupported_shapes(self):
        cases = [
            ('@host("")\ndef f() -> None:\n    pass\n', "must not be empty"),
            ('@host(1)\ndef f() -> None:\n    pass\n', "literal string"),
            ('@host("bad name")\ndef f() -> None:\n    pass\n', "ASCII identifiers"),
            ('@host(name="x")\ndef f() -> None:\n    pass\n', "literal string"),
            ('@host("x")\nasync def f() -> None:\n    return\n', "pass body"),
            ('@host("x")\ndef f() -> i32:\n    return 1\n', "pass body"),
            ('@host("x")\ndef f(x: i32 = 0) -> None:\n    pass\n', "default arguments"),
            ('@host("x")\ndef f(x: list[tuple[i32, i32]]) -> None:\n    pass\n', "host boundaries"),
            ('@host("x")\ndef f() -> str | None:\n    pass\n', "scalar, string"),
            ('def outer() -> None:\n    @host("x")\n    def inner() -> None:\n        pass\n', "top-level"),
            ('@host("same")\ndef a() -> i32:\n    pass\n@host("same")\ndef b() -> str:\n    pass\n', "conflicting signatures"),
            ('@host("same")\ndef a() -> i32:\n    pass\n@host("same")\nasync def b() -> i32:\n    pass\n', "conflicting signatures"),
        ]
        for source, message in cases:
            with self.subTest(source=source):
                self.src.write_text('from snek import host, i32\n' + source + '\ndef entry() -> None:\n    pass\n')
                with self.assertRaisesRegex(Exception, message):
                    self.build(exports=["entry"])

    def test_host_imports_require_library_and_cpython_fails_explicitly(self):
        self.src.write_text('from snek import host\n@host("text")\ndef text() -> str:\n    pass\n')
        if self.native:
            result = subprocess.run([self.native, "build", str(self.src)], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("host imports require", result.stderr)
        else:
            with self.assertRaisesRegex(Exception, "host imports require"):
                driver.build(self.src, self.root / "command.wasm", compiler=self.compiler, optimize=False)
            with self.assertRaisesRegex(Exception, "host imports require"):
                driver.compile_to_wat(self.src.read_text(), str(self.src), compiler=self.compiler, component=True)
        from snek import host
        @host("text")
        def text() -> str:
            pass
        with self.assertRaisesRegex(RuntimeError, "requires a JavaScript host"):
            text()
        import asyncio
        @host("text")
        async def async_text() -> str:
            pass
        with self.assertRaisesRegex(RuntimeError, "requires a JavaScript host"):
            asyncio.run(async_text())

    def test_async_scalar_returns_and_thenables(self):
        self.src.write_text('from snek import host, i32, f32\n' + ''.join(
            f'@host("{name}")\nasync def {name}() -> {ty}:\n    pass\n'
            for name, ty in (("integer", "i32"), ("single", "f32"), ("boolean", "bool"))) + '''
async def error() -> str:
    try:
        await integer()
        return "ok"
    except RuntimeError as problem:
        return str(problem).upper()
''')
        self.build(exports=["integer", "single", "boolean", "error"])
        self.node('''
let integer = -2147483648, single = 1.1, boolean = true;
const lib = await instantiate(bytes, {imports: {
  integer: () => integer === "reject" ? Promise.reject("Straße") : ({then(resolve) {resolve(integer);}}),
  single: async () => single, boolean: async () => boolean,
}});
assert.equal(await lib.integer(), integer);
assert.equal(await lib.single(), Math.fround(single));
assert.equal(await lib.boolean(), true);
integer = 2147483648; boolean = 1; single = "1";
await assert.rejects(lib.integer(), /i32 out of range/);
await assert.rejects(lib.boolean(), /expected boolean/);
await assert.rejects(lib.single(), /expected number/);
single = NaN; assert.ok(Number.isNaN(await lib.single()));
single = -0; assert.ok(Object.is(await lib.single(), -0));
integer = "reject"; assert.equal(await lib.error(), "HOST IMPORT INTEGER: STRASSE");
''')

    def test_async_lazy_start_memory_and_independent_instances(self):
        self.src.write_text('''from snek import host, i32
@host("text")
async def text(value: str) -> str:
    pass
async def repeat(n: i32) -> i32:
    unused = text("unused")
    total = 0
    for i in range(n):
        total += len(await text("used"))
    return total
''')
        self.build(exports=["repeat"])
        self.node('''
let memory;
const original = WebAssembly.instantiate;
WebAssembly.instantiate = async (...args) => {const instance = await original(...args); memory = instance.exports.memory; return instance;};
const imports = {text: async value => {assert.equal(value, "used"); return "🐍".repeat(20000);}};
let lib;
try {lib = await instantiate(bytes, {imports});} finally {WebAssembly.instantiate = original;}
assert.equal(await lib.repeat(1), 20000);
const size = memory.buffer.byteLength;
assert.equal(await lib.repeat(50), 1000000);
assert.equal(memory.buffer.byteLength, size);
const other = await instantiate(bytes, {imports});
assert.deepEqual(await Promise.all([lib.repeat(1), other.repeat(1)]), [20000, 20000]);
''')

    def test_async_exports_and_host_values(self):
        self.src.write_text(ASYNC_SOURCE)
        result = self.build(exports=ASYNC_EXPORTS)
        self.assertIn('"load"(arg0: string, options?: SnekCallOptions): Promise<string>;', result.dts_path.read_text())
        self.assertIn('"wait"(): PromiseLike<void>;', result.dts_path.read_text())
        self.node('''
const events = [];
const lib = await instantiate(bytes, {imports: {
  read_text: async value => value, wait: async () => {}, number: async value => value,
  scalar: async (a, b, c, d) => {assert.deepEqual([a, b, c, d], [-2147483648, Math.fround(1.1), Infinity, true]); return c;},
  render: text => {events.push(text);},
}});
assert.equal(await lib.plain(41), 42);
assert.equal(await lib.number(9007199254740993n), 9007199254740993n);
assert.equal(await lib.scalar(-2147483648, 1.1, Infinity, true), Infinity);
assert.equal(await lib.wait(), undefined);
assert.equal(await lib.load("Straße"), "STRASSE");
assert.equal(await lib.flow("snake"), "SNAKE!");
for (const value of ["", "\\0", "\\ufeffbom", "🐍中", "🐍".repeat(100000)])
  assert.equal(await lib.read_text(value), value);
assert.equal(await lib.repeat(1000), 1000);
assert.deepEqual(events, ["done", "done"]);
await assert.rejects(lib.failure(), e => e instanceof SnekError && e.message === "ValueError: async failure");
await assert.rejects(lib.plain("1"), /expected number/);
assert.equal(await lib.plain(1), 2);
assert.equal(lib.sync(3), 3);
''')

    def test_async_rejections_cleanup_validation_and_overlap(self):
        self.src.write_text(ASYNC_SOURCE)
        self.build(exports=ASYNC_EXPORTS)
        self.node('''
let mode = "ok", resolvePending, rejectPending;
const events = [];
const lib = await instantiate(bytes, {imports: {
  read_text: value => {
    if (mode === "throw") throw new Error("thrown");
    if (mode === "reject") return Promise.reject("rejected 🐍");
    if (mode === "invalid") return Promise.resolve(42);
    if (mode === "surrogate") return Promise.resolve("\\ud800");
    if (mode === "plain") return "not a promise";
    if (mode === "pending") return new Promise((resolve, reject) => {resolvePending = resolve; rejectPending = reject;});
    return Promise.resolve(value);
  }, wait: async () => {events.push("wait");}, number: async () => 1n << 63n,
  scalar: async () => "wrong", render: text => {events.push(text);},
}});
for (const [value, message] of [["throw", "thrown"], ["reject", "rejected 🐍"], ["invalid", "expected string"],
  ["surrogate", "surrogate"], ["plain", "must return a Promise"]]) {
  mode = value;
  assert.ok((await lib.recover()).includes(message));
  assert.deepEqual(events.splice(0), ["wait", "cleaned"]);
  await assert.rejects(lib.load("x"), e => e instanceof SnekError && e.message.includes(message));
  assert.deepEqual(events.splice(0), ["done"]);
}
mode = "pending";
const loading = lib.load("pending");
assert.equal(lib.sync(1), 1);
assert.equal(await lib.plain(1), 2);
resolvePending("Straße");
assert.equal(await loading, "STRASSE");
assert.deepEqual(events.splice(0), ["done"]);
const failing = lib.load("pending");
rejectPending(new Error("aborted"));
await assert.rejects(failing, /aborted/);
assert.deepEqual(events.splice(0), ["done"]);
mode = "ok";
assert.equal(await lib.load("back"), "BACK");
await assert.rejects(lib.number(1n), /i64 out of range/);
await assert.rejects(lib.scalar(1, 2, 3, false), /expected number/);
assert.equal(await lib.plain(1), 2);
''')

    def test_concurrent_async_invocations_are_isolated(self):
        self.src.write_text(ASYNC_SOURCE)
        self.build(exports=ASYNC_EXPORTS)
        self.node('''
const requests = new Map(), events = [], reentries = [];
let lib;
lib = await instantiate(bytes, {imports: {
  read_text: value => {
    assert.throws(() => lib.sync(0), /not reentrant/);
    reentries.push(assert.rejects(lib.plain(0), /not reentrant/));
    return new Promise((resolve, reject) => requests.set(value, {resolve, reject}));
  }, wait: async () => {events.push("wait");}, number: async value => value,
  scalar: async (a, b, c, d) => c, render: value => {events.push(value);}
}});
const first = lib.load("first"), second = lib.load("second");
const rejected = lib.load("rejected"), recovered = lib.recover();
const rejection = assert.rejects(rejected, /only this task fails/);
assert.deepEqual([...requests.keys()], ["first", "second", "rejected", "recover"]);
assert.equal(lib.sync(17), 17);
await assert.rejects(lib.load(42), /expected string/);
await assert.rejects(lib.failure(), /async failure/);
requests.get("second").resolve("Straße 🐍".repeat(10000));
assert.equal(await second, "STRASSE 🐍".repeat(10000));
requests.get("rejected").reject(new Error("only this task fails"));
await rejection;
requests.get("recover").resolve(42); // boundary failure belongs to this task
assert.match(await recovered, /expected string/);
requests.get("first").resolve("first 🐍");
assert.equal(await first, "FIRST 🐍");
assert.equal(events.filter(value => value === "done").length, 3);
assert.ok(events.includes("cleaned"));
assert.deepEqual(await Promise.all([lib.number(9007199254740993n), lib.scalar(1, 2, 3.5, true), lib.plain(41)]),
  [9007199254740993n, 3.5, 42]);
const many = Array.from({length: 32}, (_, i) => lib.load(String(i)));
for (let i = 31; i >= 0; i--) requests.get(String(i)).resolve("value " + i);
assert.deepEqual(await Promise.all(many), Array.from({length: 32}, (_, i) => "VALUE " + i));
await Promise.all(reentries);
''')

    def test_callbacks_can_run_between_async_steps(self):
        self.src.write_text(CALLBACK_SOURCE)
        self.build(CALLBACK_EXPORTS)
        self.node('''
let resolve;
const lib = await instantiate(bytes, {imports: {
  store: fn => {}, get_callback: () => text => text,
  get_async_callback: () => new Promise(done => {resolve = done;})
}});
const count = lib.counter(0), made = lib.make("prefix: ");
const waiting = lib.from_async_host("held");
assert.equal(count(), 1);
assert.equal(made("Straße"), "prefix: STRASSE");
const closures = await Promise.all([lib.deferred_make("one: "), lib.deferred_make("two: ")]);
assert.equal(closures[0]("a"), "one: A");
assert.equal(closures[1]("b"), "two: B");
resolve(text => text + "!");
assert.equal(await waiting, "held!");
assert.equal(count(), 2);
''')

    def test_wait_all_host_requests_and_cleanup(self):
        self.src.write_text(WAIT_ALL_SOURCE)
        self.build(WAIT_ALL_EXPORTS)
        self.node('''
const reads = new Map(), cleanups = new Map(), events = [];
let immediate = false, cleanupFails = false;
const lib = await instantiate(bytes, {imports: {
  read: name => immediate ? Promise.resolve(name) : new Promise((resolve, reject) => reads.set(name, {resolve, reject})),
  cleanup: name => {
    events.push("cleanup:" + name);
    if (cleanupFails) return name === "b" ? Promise.resolve() : Promise.reject(new Error("cleanup failure"));
    return immediate ? Promise.resolve() : new Promise(resolve => cleanups.set(name, resolve));
  }, report: text => {events.push(text);}
}});
const pair = lib.pair();
assert.deepEqual([...reads.keys()], ["a", "b"]);
reads.get("b").resolve("🐍".repeat(10000));
await new Promise(resolve => setTimeout(resolve, 0));
assert.ok(cleanups.has("b"));
cleanups.get("b")();
reads.get("a").resolve("Straße");
await new Promise(resolve => setTimeout(resolve, 0));
cleanups.get("a")();
assert.equal(await pair, "finished");
assert.ok(events.indexOf("done:b") < events.indexOf("done:a"));
cleanupFails = true;
const hostFailure = lib.pair();
const hostRejected = assert.rejects(hostFailure, /host failure/);
reads.get("b").reject(new Error("host failure"));
await hostRejected;
cleanupFails = false;
events.length = 0; reads.clear(); cleanups.clear();
let settled = false;
const failure = lib.failfast();
const rejected = assert.rejects(failure, /ValueError: first failure/).then(() => {settled = true;});
// Checkpoints must progress even though another child has an unresolved host request.
for (let i = 0; i < 30 && !cleanups.has("blocked"); i++) await new Promise(resolve => setTimeout(resolve, 2));
assert.ok(cleanups.has("blocked"));
assert.equal(settled, false); // awaited cleanup must finish before failure escapes
cleanups.get("blocked")();
await rejected;
assert.deepEqual(events, ["cleanup:blocked", "done:blocked"]);
reads.get("blocked").reject(new Error("late rejection is handled"));
immediate = true;
assert.equal(await lib.duplicate(), "reuse");
assert.match(await lib.reused(), /distinct, unawaited/);
immediate = false; cleanupFails = true;
await assert.rejects(lib.nested(), /ValueError: first failure/);
assert.ok(events.includes("cleanup:outer"));
immediate = true; cleanupFails = false;
assert.equal(await lib.pair(), "finished");
const callbackRequests = new Map();
const callbacks = lib.callbacks(name => new Promise(resolve => callbackRequests.set(name, resolve)));
assert.deepEqual([...callbackRequests.keys()], ["one", "two"]);
callbackRequests.get("two")("second"); callbackRequests.get("one")("first");
await callbacks;
assert.ok(events.includes("first") && events.includes("second"));
''')

    def test_gather_preserves_input_order_and_structured_cleanup(self):
        self.src.write_text(WAIT_ALL_SOURCE)
        self.build(WAIT_ALL_EXPORTS)
        self.node('''
const requests = new Map(), cleaned = [];
const lib = await instantiate(bytes, {imports: {
  read: name => new Promise(resolve => requests.set(name, resolve)),
  cleanup: async name => {await new Promise(resolve => setTimeout(resolve, 0)); cleaned.push(name);},
  report: text => {}
}});
let settled = false;
const task = lib.gathered().then(value => {settled = true; return value;});
assert.deepEqual([...requests.keys()], ["a", "b"]);
const second = "🐍".repeat(10000);
requests.get("b")(second);
await new Promise(resolve => setTimeout(resolve, 5));
assert.equal(settled, false);
requests.get("a")("first");
assert.equal(await task, "first|" + second);
assert.deepEqual(cleaned, ["b", "a"]);
await assert.rejects(lib.gather_failure(), /gather failure/);
assert.deepEqual(cleaned, ["b", "a", "blocked"]);
''')

    def test_gather_opaque_reference_results(self):
        self.src.write_text('''from snek import HostObject, host, gather
@host("resolve")
async def resolve(value: HostObject | None) -> HostObject | None:
    pass
async def check(a: HostObject, b: HostObject) -> bool:
    values = await gather(resolve(a), resolve(None), resolve(b))
    return values[0] is a and values[1] is None and values[2] is b
''')
        self.build(["check"])
        self.node('const lib = await instantiate(bytes, {imports: {resolve: async value => value}}); assert.equal(await lib.check({}, {}), true);')

    def test_abort_signal_unwinds_groups_and_awaits_cleanup(self):
        self.src.write_text(WAIT_ALL_SOURCE)
        result = self.build(WAIT_ALL_EXPORTS)
        self.assertIn("options?: SnekCallOptions", result.dts_path.read_text())
        self.node('''
const {SnekCancelledError} = await import("./output.js");
const requests = new Map(), cleanup = new Map(), events = [];
const lib = await instantiate(bytes, {imports: {
  read: name => new Promise((resolve, reject) => requests.set(name, {resolve, reject})),
  cleanup: name => new Promise(resolve => cleanup.set(name, resolve)),
  report: text => {events.push(text);}
}});
const already = new AbortController(); already.abort("before start");
await assert.rejects(lib.pair({signal: already.signal}), e => e instanceof SnekCancelledError && e.cause === "before start");
assert.equal(requests.size, 0);
await assert.rejects(lib.pair(null), /call options/);
await assert.rejects(lib.pair({signal: {}}), /AbortSignal/);
await assert.rejects(lib.pair({signal: null}), /AbortSignal/);
const control = new AbortController(), reason = new Error("user cancelled");
let added = 0, removed = 0;
const add = control.signal.addEventListener.bind(control.signal), remove = control.signal.removeEventListener.bind(control.signal);
control.signal.addEventListener = (...args) => {added++; return add(...args);};
control.signal.removeEventListener = (...args) => {removed++; return remove(...args);};
let settled = false;
const pending = lib.gathered({signal: control.signal});
const rejected = assert.rejects(pending, error => error instanceof SnekCancelledError && error.cause === reason).then(() => {settled = true;});
const unrelated = lib.duplicate();
control.abort(reason);
for (let i = 0; i < 30 && cleanup.size < 2; i++) await new Promise(resolve => setTimeout(resolve, 2));
assert.ok(cleanup.has("a") && cleanup.has("b"));
assert.equal(settled, false);
cleanup.get("b")(); cleanup.get("a")();
await rejected;
assert.equal(added, 1); assert.equal(removed, 1);
assert.ok(events.includes("done:a") && events.includes("done:b"));
requests.get("a").reject(new Error("late rejection")); requests.get("b").resolve("late result");
requests.get("reuse").resolve("unrelated");
await new Promise(resolve => setTimeout(resolve, 0));
cleanup.get("reuse")();
assert.equal(await unrelated, "unrelated");
''')

    def test_abort_during_host_call_and_cleanup_failure(self):
        self.src.write_text(ASYNC_SOURCE + '''
async def cleanup_failure() -> None:
    try:
        await read_text("url")
    finally:
        await checkpoint()
        raise ValueError("Snek cleanup failed")
async def explicit_exit() -> None:
    raise GeneratorExit()
async def suppress() -> str:
    try:
        return await read_text("url")
    except GeneratorExit:
        return "suppressed"
async def looping() -> None:
    try:
        while True:
            await checkpoint()
    finally:
        print("loop cleaned")
''')
        self.build([*ASYNC_EXPORTS, "cleanup_failure", "explicit_exit", "suppress", "looping"])
        self.node('''
const {SnekCancelledError} = await import("./output.js");
let controller = new AbortController(), fail = false, events = [], output = "";
const lib = await instantiate(bytes, {stdout: text => output += text, imports: {
  read_text: value => {controller.abort(); return new Promise(() => {});},
  wait: async () => {events.push("wait");}, number: async value => value,
  scalar: async (a, b, c, d) => c, render: text => {if (fail) throw new Error("cleanup failed"); events.push(text);}
}});
await assert.rejects(lib.load("url", {signal: controller.signal}), SnekCancelledError);
assert.deepEqual(events, ["done"]);
controller = new AbortController(); events = [];
await assert.rejects(lib.recover({signal: controller.signal}), SnekCancelledError);
assert.deepEqual(events, ["wait", "cleaned"]);
controller = new AbortController(); fail = true;
await assert.rejects(lib.load("url", {signal: controller.signal}), /cleanup failed/);
fail = false;
const complete = new AbortController();
assert.equal(await lib.plain(41, {signal: complete.signal}), 42);
complete.abort();
assert.equal(await lib.plain(1), 2);
controller = new AbortController();
await assert.rejects(lib.cleanup_failure({signal: controller.signal}), error => error instanceof SnekError && error.message.includes("Snek cleanup failed"));
await assert.rejects(lib.explicit_exit(), error => error instanceof SnekError && error.message.includes("GeneratorExit"));
controller = new AbortController();
await assert.rejects(lib.suppress({signal: controller.signal}), SnekCancelledError);
controller = new AbortController();
const looping = lib.looping({signal: controller.signal});
controller.abort();
await assert.rejects(looping, SnekCancelledError);
assert.equal(output, "loop cleaned\\n");
''')

    def test_library_abi_mismatch_is_rejected(self):
        self.build()
        self.node('''
const original = WebAssembly.instantiate;
try {
  for (const version of [undefined, 1, 2, 3, 5]) {
    WebAssembly.instantiate = async (...args) => {
      const instance = await original(...args);
      return {exports: {...instance.exports, __snek_abi: version === undefined ? undefined : new WebAssembly.Global({value: "i32"}, version)}};
    };
    await assert.rejects(instantiate(bytes), /Invalid Snek library exports/);
  }
} finally {WebAssembly.instantiate = original;}
assert.equal((await instantiate(bytes)).add(1, 2), 3);
''')

    def test_browser_loader_response_and_url(self):
        self.build()
        self.node('''
const lib = await instantiate(new Response(bytes));
assert.equal(lib.add(2, 3), 5);
await assert.rejects(instantiate(new Response("bad", {status: 404})), /404/);
const url = URL.createObjectURL(new Blob([bytes], {type: "application/wasm"}));
try { assert.equal((await instantiate(new URL(url))).add(4, 5), 9); }
finally { URL.revokeObjectURL(url); }
''')

    def test_loader_diagnostics_and_capabilities(self):
        self.build()
        self.node('''
const {assertSupported, SnekLoadError, SnekCompatibilityError} = await import("./output.js");
assertSupported();
await assert.rejects(instantiate(new Uint8Array([1, 2, 3])), e => e instanceof SnekLoadError && e.cause instanceof WebAssembly.CompileError);
await assert.rejects(instantiate(new Uint8Array([0,97,115,109,1,0,0,0])), /Invalid Snek library exports/);
const used = new Response(bytes); await used.arrayBuffer();
await assert.rejects(instantiate(used), e => e instanceof SnekLoadError && /response body/.test(e.message) && e.cause instanceof TypeError);
const saved = globalThis.WebAssembly;
try {
  globalThis.WebAssembly = undefined;
  await assert.rejects(instantiate(bytes), SnekCompatibilityError);
} finally { globalThis.WebAssembly = saved; }
''')

    def test_imports_are_internal(self):
        self.src.write_text('from helper import twice\nfrom snek import i32\ndef value(x: i32) -> i32:\n    return twice(x)\n')
        (self.root / "helper.py").write_text('from snek import i32\nprint("helper init")\ndef twice(x: i32) -> i32:\n    return x * 2\n')
        self.build(["value"])
        self.node('const lib = await instantiate(bytes); assert.equal(lib.value(21), 42); assert.equal(lib.twice, undefined);')
        with self.assertRaises((ValueError, RuntimeError)): self.build(["twice"])

    def test_library_does_not_run_main_guard(self):
        self.src.write_text('def name() -> str:\n    return __name__\nif __name__ == "__main__":\n    raise ValueError("command only")\nelse:\n    print("library initialized")\n')
        self.build(["name"])
        self.node('let output = ""; const lib = await instantiate(bytes, {stdout: t => output += t}); assert.equal(lib.name(), "entry"); assert.equal(output, "library initialized\\n");')

    def test_gc_objects_and_unicode_stay_internal(self):
        self.src.write_text('''from snek import i32
class Box:
    value: str
    def __init__(self, value: str) -> None:
        self.value = value
    def result(self) -> str:
        return "|".join([self.value.lower(), self.value.upper()])
def transform(value: str) -> str:
    return Box(value).result()
def alpha(value: str) -> bool:
    return value.isalpha()
def number(value: str) -> i32:
    return i32(value)
''')
        self.build(["transform", "alpha", "number"])
        self.node('''const lib = await instantiate(bytes);
assert.equal(lib.transform("Straße 🐍"), "straße 🐍|STRASSE 🐍");
assert.equal(lib.transform("ΟΣ"), "ος|ΟΣ");
assert.equal(lib.alpha("中文é"), true);
assert.equal(lib.alpha("🐍"), false);
assert.equal(lib.number("٤٢"), 42);
''')

    def test_invalid_exports(self):
        for exports in (["missing"], ["add", "add"], ["a.b"], ["memory"], ["then"], ["__snek_init"]):
            with self.subTest(exports=exports), self.assertRaises((ValueError, RuntimeError)):
                self.build(exports)
        self.assertFalse((self.root / "output.wasm").exists())

    def test_unsupported_boundaries_and_services(self):
        for source in (
            'def value(x: list[tuple[str, str]]) -> str:\n    return x[0][0]\n',
            'def value() -> list[tuple[str, str]]:\n    return [("x", "y")]\n',
            'async def value() -> list[tuple[str, str]]:\n    return [("x", "y")]\n',
            'def value(x: list[list[str] | None]) -> None:\n    pass\n',
            'from snek import sys\ndef value() -> str:\n    return sys.argv[0]\n',
            'from snek import time, f64\ndef value() -> f64:\n    return time.time()\n',
        ):
            with self.subTest(source=source):
                self.src.write_text(source)
                with self.assertRaises((ValueError, RuntimeError)): self.build(["value"])

    def test_failed_init_and_callback(self):
        self.src.write_text('def value() -> str:\n    return "ok"\nraise ValueError("init failed")\n')
        self.build(["value"])
        self.node('await assert.rejects(instantiate(bytes), /init failed/);')
        self.src.write_text(SOURCE)
        self.build()
        self.node('''
let ready = false;
const lib = await instantiate(bytes, {stdout: () => {if (ready) throw new Error("callback failed");}});
ready = true;
assert.throws(() => lib.show("test"), /callback failed/);
ready = false;
assert.equal(lib.add(2, 3), 5);
''')

    def test_no_host_compiler_fallback(self):
        if self.compiler != "snek" or self.native:
            self.build()
            return
        with ExitStack() as guards:
            for name in ("ast.parse", "snekc.check.check_module", "snekc.emit_wat.emit_module", "snekc.wasm.encode",
                         "snekc.driver.compile_to_wat", "snekc.driver.encode", "snekc.project.Project.owned_tree"):
                guards.enter_context(patch(name, side_effect=AssertionError("host fallback")))
            self.build()
        self.node('assert.equal((await instantiate(bytes)).add(1, 2), 3);')

    def test_compile_failure_preserves_all_artifacts(self):
        self.build()
        paths = [self.root / ("output" + ext) for ext in (".wasm", ".js", ".d.ts")]
        before = [path.read_bytes() for path in paths]
        with self.assertRaises((ValueError, RuntimeError)): self.build(["missing"])
        self.assertEqual([path.read_bytes() for path in paths], before)

    def test_boundary_defaults_are_required_and_private_names_safe(self):
        self.src.write_text('from snek import i32\ndef __snek_alloc(x: i32) -> i32:\n    return x + 1\ndef value(x: i32 = 5) -> i32:\n    return __snek_alloc(x)\ndef __proto__() -> str:\n    return "safe"\n')
        self.build(["value", "__proto__"])
        self.node('const lib = await instantiate(bytes); assert.equal(lib.value(5), 6); assert.throws(() => lib.value(), /expects 1/); assert.equal(lib.__proto__(), "safe");')

    def test_output_paths_cannot_alias_source(self):
        original = self.src.read_bytes()
        if os.name == "nt":
            # No symlink privilege is assumed by the Windows package job.
            self.src = self.root / "output.js"
            self.src.write_bytes(original)
        else:
            (self.root / "output.js").symlink_to(self.src)
        with self.assertRaises((ValueError, RuntimeError, driver.ToolError)): self.build()
        self.assertEqual(self.src.read_bytes(), original)
        self.assertFalse((self.root / "output.wasm").exists())

    def test_output_paths_must_be_distinct(self):
        with self.assertRaises((ValueError, RuntimeError, driver.ToolError)):
            self.build(output="output.js")
        self.assertFalse((self.root / "output.js").exists())

    def test_library_cli_validation(self):
        command = [self.native] if self.native else [sys.executable, "-m", "snekc"]
        for flags in (["--library"], ["--export", "add"], ["--library", "--export", "add", "--component"]):
            with self.subTest(flags=flags):
                result = subprocess.run([*command, "build", str(self.src), *flags], cwd=ROOT,
                                        capture_output=True, timeout=60)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn(b"Traceback", result.stderr)
        self.assertFalse(self.src.with_suffix(".wasm").exists())

    def test_text_and_binary_builds_share_bindings(self):
        if self.native:
            # The native CLI's library path deliberately returns binary directly.
            self.build()
            return
        first = self.build()
        metadata = (first.js_path.read_bytes(), first.dts_path.read_bytes())
        result = self.build(keep_wat=True)
        self.assertIn('(export "__snek_init")', result.wat)
        self.assertNotIn('(export "_start")', result.wat)
        self.assertEqual(metadata, (result.js_path.read_bytes(), result.dts_path.read_bytes()))
        self.node('assert.equal((await instantiate(bytes)).text("Hello", " 🐍"), "Hello 🐍");')


if __name__ == "__main__":
    unittest.main()
