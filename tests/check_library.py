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

    def test_opaque_host_objects(self):
        self.src.write_text(OBJECT_SOURCE)
        result = self.build(OBJECT_EXPORTS)
        self.assertIn('"echo"(arg0: object): object;', result.dts_path.read_text())
        self.assertIn('"optional_async"(arg0: object | null): Promise<object | null>;', result.dts_path.read_text())
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

    def test_unsupported_callback_signatures(self):
        for signature in ("Callable[[list[str]], str]", "Callable[[str], list[str]]",
                          "Callable[[Callable[[], str]], str]", "Callable[[], Coroutine[str]]",
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
            ('@host("x")\ndef f(x: list[i32]) -> None:\n    pass\n', "host boundaries"),
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
        self.assertIn('"load"(arg0: string): Promise<string>;', result.dts_path.read_text())
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
assert.throws(() => lib.sync(1), /not reentrant/);
await assert.rejects(lib.plain(1), /not reentrant/);
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
            'def value(x: list[str]) -> str:\n    return x[0]\n',
            'def value() -> list[str]:\n    return ["x"]\n',
            'async def value() -> list[str]:\n    return ["x"]\n',
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
