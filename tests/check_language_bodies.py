"""Self-hosted checker coverage: formatting, closures, suspension and streams."""
from __future__ import annotations

import itertools
import random
import unittest
from unittest.mock import patch

import check_bodies as base
import check_selfhost_bodies as selfhost
import check_formatting as formatting
import check_closures as closures
from bootstrap.formats import body_format
from bootstrap.parser import Node
from snekc.formatting import parse_spec
from snekc.types import STR, I64, F64, BOOL


class LanguageTests(selfhost.SelfCheckTests):
    def test_expanded_examples(self):
        for name in ("caesar", "exceptions", "finally_flow", "format_specs", "fstring_fusion",
                     "hello", "inheritance", "named_unicode", "shapes", "special_methods",
                     "str_codepoints", "strings", "uncaught", "tuples", "collection_order",
                     "dicts", "list_methods", "context_managers", "context_cleanup", "review_regressions"):
            with self.subTest(name=name):
                self.compare((base.ROOT / f"examples/{name}.py").read_text(), runtime_divergence=True)

    def test_format_spec_validation(self):
        rng = random.Random(607)
        specs = ["", "00", "0>08", "🦊^12.3s", "2147483648", ".2147483648f", "00000000000003d",
                 "invalid", "10.2ff", "🦊🦊", ".f", "n", "z", "#c", "=s", "\n^8", "0=8c"]
        for _ in range(12000):
            specs.append(rng.choice(["", "<", ">", "^", "=", "*<", "🦊^", "0="])
                         + rng.choice(["", "+", "-", " "]) + rng.choice(["", "z"])
                         + rng.choice(["", "#"]) + rng.choice(["", "0"])
                         + rng.choice(["", "1", "8", "0012"]) + rng.choice(["", ",", "_"])
                         + rng.choice(["", ".0", ".2"]) + rng.choice(list("bcdeEfFgGnosxX%") + [""]))
        for ty, host in (("str", STR), ("i64", I64), ("f64", F64), ("bool", BOOL)):
            for spec in specs:
                try:
                    expected = parse_spec(spec, host, None)
                except base.CompileError:
                    with self.assertRaises(ValueError, msg=(ty, spec)):
                        body_format(spec, ty, Node("test"))
                else:
                    actual = body_format(spec, ty, Node("test"))
                    self.assertEqual(vars(actual), vars(expected), (ty, spec))

    def test_closures_and_suspension(self):
        for name in ("closures", "closures_cleanup", "closures_methods", "closures_order", "closures_suspended",
                     "uncaught_closure", "keyed_builtins", "keyed_order", "keyed_stability", "keyed_async",
                     "async_functions", "async_order", "async_output", "generator_reentrant", "generator_values",
                     "generators", "generator_cleanup", "suspended_methods", "stdlib_host"):
            with self.subTest(name=name):
                # Node's preview1 host rejects this fixture's empty environment
                # in the reference compiler too. Preserve exact parity with
                # that runtime; exercise populated host configuration below.
                self.compare((base.ROOT / f"examples/{name}.py").read_text(), runtime_divergence=name == "stdlib_host")

    def test_closure_invariants_without_fallback(self):
        def check(source):
            source = 'from snek import i32\nfrom typing import Callable\n' + source
            return base.to_module(base.check_source(source, self.wasm))
        with patch.object(base.Checker, "run", side_effect=AssertionError("Python checker fallback")), \
             patch("ast.parse", side_effect=AssertionError("Python parser fallback")), \
             patch("snekc.closures.lower_cells", side_effect=AssertionError("Python closure lowering fallback")):
            for name in unittest.defaultTestLoader.getTestCaseNames(closures.ClosureTests):
                probe = closures.ClosureTests(name)
                probe.check = check
                getattr(probe, name)()

    def test_standard_library_binary_streams(self):
        data = bytes(range(256)) * 300
        for name in ("stdlib_pipe", "stdlib_streams"):
            source = (base.ROOT / f"examples/{name}.py").read_text()
            owned = base.compile_source(source, self.wasm)
            reference = base.driver.compile_to_wat(source)
            outputs = []
            for index, wat in enumerate((reference, owned)):
                path = self.directory / f"{name}-{index}.wasm"
                path.write_bytes(base.encode(wat))
                result = base.runner.run_binary(path, stdin=data)
                outputs.append((result.code, result.stdout, result.stderr))
            self.assertEqual(outputs[0], outputs[1])
            self.assertEqual(outputs[1][0], 0)
            if name == "stdlib_pipe":
                self.assertEqual(outputs[1][1:], (data, b""))

    def test_standard_library_host_configuration(self):
        source = '''from snek import os, sys, time, secrets, run
async def main() -> None:
    print(sys.argv[1:], os.getenv("SNEK_TEST"), os.getenv("MISSING", "fallback"))
    print(time.time() > 0.0, time.monotonic() > 0.0)
    await time.sleep(0.0)
    print(len(secrets.token_bytes(16)), len(secrets.token_hex(8)), secrets.randbelow(1))
run(main())
'''
        path = self.binary(source, "host-configuration")
        result = base.runner.run_binary(path, ["a", "β"], env={"SNEK_TEST": "value"})
        self.assertEqual((result.code, result.stdout, result.stderr),
                         (0, "['a', 'β'] value fallback\nTrue True\n16 16 0\n".encode(), b""))

    def test_stream_values_and_cleanup(self):
        self.compare('''from snek import sys, io, i32, run
from snek.io import TextOutput as Output
from typing import Callable
class Writer:
    stream: Output
    def __init__(self, stream: Output) -> None: self.stream = stream
    def write(self, text: str) -> i32: return self.stream.write(text)
def callback(stream: Output) -> Callable[[str], i32]:
    return lambda text: stream.write(text)
def choose(stream: io.TextOutput | None) -> io.TextOutput:
    return sys.stdout if stream is None else stream
async def main() -> None:
    stream = choose(None)
    print(stream is sys.stdout, stream.closed, stream.buffer.closed)
    print(stream == sys.stdout, stream != sys.stderr, stream == stream.buffer)
    print(Writer(stream).write("writer:"), callback(stream)("callback:"))
    await stream.drain()
    error = sys.stderr
    sink = error.buffer
    try:
        with error.buffer as sink:
            sink.write([])
            raise ValueError("cleanup")
    except ValueError as exc:
        print(str(exc), error.closed, sink.closed)
    try: error.write("not written")
    except ValueError: print("closed write")
    try:
        with error: print("not entered")
    except ValueError: print("closed enter")
run(main())
''')

    def test_stream_sized_reads(self):
        source = '''from snek import sys, io, run
async def main() -> None:
    stream: io.BinaryInput = sys.stdin.buffer
    print(await stream.read(0))
    print(await stream.read(2))
    print(await stream.readline(3))
    print(await stream.readline())
    print(await stream.read())
    print(await stream.read())
    stream.close()
    print(sys.stdin.closed)
    try: await stream.read()
    except ValueError: print("closed read")
run(main())
'''
        outputs = []
        for index, wat in enumerate((base.driver.compile_to_wat(source), base.compile_source(source, self.wasm))):
            path = self.directory / f"stream-sized-{index}.wasm"
            path.write_bytes(base.encode(wat))
            got = base.runner.run_binary(path, stdin=b"ab123456\nrest\xff")
            outputs.append((got.code, got.stdout, got.stderr))
        self.assertEqual(outputs[0], outputs[1])
        self.assertEqual(outputs[1], (0, b"[]\n[97, 98]\n[49, 50, 51]\n[52, 53, 54, 10]\n[114, 101, 115, 116, 255]\n[]\nTrue\nclosed read\n", b""))

    def test_expanded_component_cores(self):
        for name in ("context_cleanup", "closures_suspended", "keyed_async", "generator_cleanup",
                     "fstring_fusion", "stdlib_pipe", "stdlib_streams", "stdlib_host"):
            source = (base.ROOT / f"examples/{name}.py").read_text()
            wat = base.compile_source(source, self.wasm, component=True)
            path = self.directory / f"{name}.core.wasm"
            path.write_bytes(base.encode(wat))
            self.assertNotIn('"wasi_snapshot_preview1"', wat)
            if base.shutil.which("wasm-tools"):
                result = base.subprocess.run(["wasm-tools", "validate", str(path)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_format_differential_stress(self):
        def build(src, wasm, optimize):
            wat = wasm.with_suffix(".wat")
            wat.write_text(base.compile_source(src.read_text(), self.wasm))
            base.driver.assemble(wat, wasm, optimize=optimize and bool(base.os.environ.get("SNEK_WASM_OPT")))
        with patch.object(formatting.driver, "build", side_effect=build):
            formatting.main()

    def test_tuple_effects_and_slices(self):
        source = '''from snek import i32
def value(label: str) -> tuple[str, i32, bool]:
    print(label)
    return (label, 2, True)
print(value("a") + value("b"))
print(value("zero") * 0, -1 * value("negative"), value("twice") * 2)
print(value("empty")[1:1], value("reverse")[::-1])
'''
        bounds = [None, -8, -3, -1, 0, 1, 3, 8, -(2**100), 2**100]
        source += 'def slices() -> None:\n    t = ("s", 2, True)\n'
        for start, stop, step in itertools.product(bounds, bounds, [None, -9, -2, -1, 1, 2, 9]):
            spec = ":".join("" if b is None else str(b) for b in (start, stop, step))
            source += f"    print(t[{spec}])\n"
        source += 'slices()\n'
        self.compare(source)

    def test_captured_builtin_names_and_class_values(self):
        self.compare('''from snek import i32
from typing import Callable
class Base: pass
class Child(Base): pass
def choose(flag: bool) -> type[Base]: return Base if flag else Child
def outer() -> Callable[[], i32]:
    print: Callable[[i32], i32] = lambda x: x + 1
    def inner() -> i32:
        return print(4)
    return inner
print(outer()(), choose(True).__name__, choose(False).__name__)
''')

    def test_language_errors(self):
        paths = sorted((base.ROOT / "tests/errors").glob("format_*.py"))
        paths += sorted((base.ROOT / "tests/errors").glob("with_*.py"))
        sources = [path.read_text() for path in paths]
        sources += ["print((1, 2)[::0])", "n = 2\nprint((1,) * n)", "print((1,) * 10001)",
                    "n = 1\nprint((1, 2)[n:])", "print(f'{1!a}')", "print(type(1))",
                    "from types import TracebackType\ndef f(tb: TracebackType | None) -> None:\n    print(tb == tb)"]
        sources += ["from types import TracebackType\ndef f(tb: TracebackType | None) -> None:\n    print(tb is tb)"]
        for source in sources:
            with self.subTest(source=source):
                with self.assertRaises(ValueError): base.check(source)
                with self.assertRaises(base.CompileError): base.driver.compile_to_wat(source)
                result = base.runner.run_binary(self.wasm, stdin=source.encode())
                self.assertNotEqual(result.code, 0)
                self.assertEqual(result.stdout, b"")


if __name__ == "__main__":
    unittest.main()
