"""Scalar Snek checker: owned typed IR parity, diagnostics and executable proofs."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import random
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bootstrap.build_bodies import bundle_source, check
from bootstrap.build_declarations import declare
from bootstrap.body_bridge import check_source, compile_source, to_module
from snekc import driver, runner
from snekc.check import Checker
from snekc.errors import CompileError
from snekc.emit_wat import emit_module
from snekc.frontend import parse_source
from snekc.wasm import encode


PROGRAMS = {
    "branches": '''from snek import i32
def f(c: bool) -> i32:
    if c:
        n = 3
    else:
        n = 4
    return n
def g(c: bool) -> i32:
    if c:
        return 9
    else:
        value = 8
    return value
print(f(True), f(False), g(True), g(False))
''',
    "optional_flow": '''from snek import i32
def length(s: str | None) -> i32:
    if s is not None and len(s) > 0:
        return len(s)
    if s is None or len(s) == 0:
        return 0
    return 99
def choose(s: str | None) -> str:
    return s if s is not None else "fallback"
def asserted(s: str | None) -> i32:
    assert s is not None, "missing"
    return len(s)
print(length(None), length(""), length("abc"), choose(None), choose("ok"), asserted("x"))
''',
    "argument_order": '''from snek import i32
def tick(n: i32) -> i32:
    print(n, end=";")
    return n
def combine(a: i32, b: i32 = 9, c: i32 = 8) -> i32:
    return a * 100 + b * 10 + c
print(combine(tick(1), c=tick(3), b=tick(2)))
print(tick(4) + combine(c=tick(7), a=tick(5), b=tick(6)))
print(tick(8) < combine(b=tick(2), a=tick(3)))
print(False and tick(9) > 0, True or tick(10) > 0)
print(tick(11) if True else tick(12))
''',
    "range_order": '''from snek import i32, i64
def bound(n: i32) -> i32:
    print(n, end=";")
    return n
def wide() -> i64: return 2147483649
def main() -> None:
    total = 0
    for i in range(bound(1), bound(5), 2):
        total += i
    print(total)
    for j in range(2147483647, wide()):
        print(j)
    n: i64 = 0
    for n in range(3):
        pass
    print(n)
main()
''',
    "nested_loops": '''from snek import i32
def f() -> i32:
    total = 0
    for i in range(4):
        j = 0
        while j < 5:
            j += 1
            if j == 2: continue
            if i == 3: break
            total += i + j
    return total
print(f())
''',
    "strings": '''from snek import i32
def describe(s: str | None) -> str:
    return str(s) + ":" + repr(s)
print("a" + "☃", "α" < "β", "x" != "y", len("👩‍💻"), "☃🐍"[-1])
print(describe(None), describe("'\\t"), ord("🐍"), repr(chr(0x2028)))
def dynamic(cp: i32) -> str: return repr(chr(cp))
print(dynamic(0xe000), dynamic(0xa0), dynamic(0x2603))
''',
    "numbers": '''from snek import i32, i64, f32, f64
def wide(x: i64) -> i64: return x
def floats(x: f64, y: f64 = 0.5) -> f64: return x / y
def main() -> None:
    n: i32 = 3
    b: i64 = 1
    b = n
    f: f32 = abs(-0.5)
    print(wide(n), b, f, floats(2.0), floats(y=2.0, x=7.0))
    print(-7 // 3, 7 // -3, -7 % 3, 7 % -3, -7.5 // 2.0, -7.5 % 2.0)
    print(7 / 2, n + 0.5, 0.5 + n, i64(1) << 40, i32(f64(n) / 2.0))
    print(abs(-2), min(1, 2), max(2.0, 3.0), f64(True), bool(0.0), bool(-0.5))
    print(0.1 + 0.2, 1e-300, -0.0, 1e99999, 1e-99999999)
main()
''',
    "defaults": '''from snek import i32, i64, f64
def f(x: "i64" = -0x8000000000000000, s: str | None = None, d: f64 = 1.25) -> str:
    return str(x) + ":" + str(s) + ":" + str(d)
print(f(), f(d=0.5, s="x", x=3))
''',
    "scope_annotations": '''from snek import i32, i64
def f() -> i64:
    value = 2147483648
    value: i64
    return value
def g() -> str | None:
    text = None
    text: str | None
    return text
print(f(), g())
''',
    "assert_laziness": '''def message() -> str:
    print("should not run")
    return "bad"
assert True, message()
print("ok")
''',
}

ERRORS = [
    ("def f() -> str:\n    return missing", "undefined name"),
    ("from snek import i32\ndef f() -> i32:\n    print(x)\n    x = 1\n    return x", "before it is assigned"),
    ("from snek import i32\ndef f(c: bool) -> i32:\n    if c: x = 1\n    return x", "before it is assigned"),
    ("from snek import i32\ndef f() -> i32:\n    for i in range(1): x = 1\n    return x", "before it is assigned"),
    ("from snek import i32\ndef f() -> i32:\n    while False: x = 1\n    return x", "before it is assigned"),
    ("from snek import i32\ndef f() -> i32:\n    for i in range(1): pass\n    return i", "before it is assigned"),
    ("def f() -> None:\n    print(1)\n    print = 2", "before it is assigned"),
    ("def f() -> None:\n    x = 1\n    x = 'no'", "expected i32"),
    ("from snek import i32, i64\ndef f() -> None:\n    x: i32 = 1\n    x: i64 = 2", "already declared"),
    ("def f() -> None:\n    x = None", "cannot infer"),
    ("def f() -> None:\n    x: None", "variable cannot"),
    ("from snek import i32\ndef f(c: bool) -> i32:\n    if c: return 1", "reach the end"),
    ("def f() -> str:\n    return", "must return"),
    ("def f() -> str:\n    return 2", "expected str"),
    ("def f() -> None:\n    return 2", "cannot return"),
    ("if __name__ == '__main__':\n    return", "return outside"),
    ("def f() -> None:\n    break", "outside a loop"),
    ("def f() -> None:\n    continue", "outside a loop"),
    ("if 1: print('no')", "condition must be bool"),
    ("print(1 and True)", "condition must be bool"),
    ("from snek import i32\ndef f(s: str | None) -> i32: return len(s)", "non-optional"),
    ("from snek import i32\ndef f() -> i32:\n    s: str | None = 'yes'\n    while False: s = None\n    return len(s)", "non-optional"),
    ("from snek import i32\ndef f(c: bool) -> i32:\n    s: str | None = None\n    if c: s = 'x'\n    return len(s)", "non-optional"),
    ("print('x' is None)", "never None"),
    ("print(1 < 2 < 3)", "chained comparisons"),
    ("print(True + 1)", "incompatible operands"),
    ("print('x' + 1)", "incompatible operands"),
    ("print(1.0 << 2)", "integer operands"),
    ("from snek import i64\nprint(i64(2) / 2)", "/ on i64"),
    ("from snek import f64\ndef f() -> None:\n    x: f64 = 1", "integer literal"),
    ("from snek import f64\ndef f(x: f64) -> None: pass\nf(1)", "integer literal"),
    ("from snek import f64\ndef f() -> f64: return 2 + 3", "ints only mix"),
    ("def f(c: bool) -> None:\n    x = 1 if c else 2.5", "int at run time"),
    ("print(1 if True else 2.5)", "int at run time"),
    ("print(min(1.5, 2))", "integer literal"),
    ("from snek import i32\ndef f(x: i32) -> None: pass\nf()", "missing required"),
    ("from snek import i32\ndef f(x: i32) -> None: pass\nf(1, 2)", "too many arguments"),
    ("from snek import i32\ndef f(x: i32) -> None: pass\nf(y=1)", "unexpected keyword"),
    ("from snek import i32\ndef f(x: i32) -> None: pass\nf(1, x=2)", "multiple values"),
    ("print(1, sep=2)", "literal string sep/end"),
    ("def f() -> None:\n    for i in range(2, 5, 0): pass", "step cannot be zero"),
    ("def f() -> None:\n    for i in range(1.0): pass", "integer type"),
    ("from snek import i32, i64\ndef f() -> None:\n    i: i32 = 0\n    end: i64 = 4\n    for i in range(end): pass", "expected i32"),
]

UNPORTED = [
    "print('x'.format())",
    "from snek import f64\nprint(f64('1.2'))",
]


class BodyFixture:
    """Reusable seed-checker fixture and differential assertions; no test cases."""
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="snek-body-tests-")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.directory = Path(cls.temp.name)
        cls.source = bundle_source()
        cls.path = cls.directory / "bodies.bundle.py"
        cls.path.write_text(cls.source)
        cls.wasm = cls.directory / "bodies.wasm"
        driver.build(cls.path, cls.wasm, optimize=False, frontend="snek")

    def python_result(self, source):
        result = subprocess.run([sys.executable, "-c", source], capture_output=True, text=True,
                                env=dict(os.environ, PYTHONPATH=str(ROOT)))
        return runner.RunResult(result.returncode, result.stdout, result.stderr)

    def executable(self, wat, optimize=False):
        src = self.directory / "program.wat"
        wasm = self.directory / "program.wasm"
        src.write_text(wat)
        driver.assemble(src, wasm, optimize=optimize)
        return runner.run(wasm)

    def same_execution(self, actual, expected):
        self.assertEqual(actual.stdout, expected.stdout)
        self.assertEqual(actual.code == 0, expected.code == 0, actual.stderr)
        if expected.code:
            self.assertEqual(actual.stderr.strip().splitlines()[-1], expected.stderr.strip().splitlines()[-1])
        else:
            self.assertEqual(actual.stderr, "")

    def compare(self, source, *, optimize=True, runtime_divergence=False):
        owned = check_source(source)
        from_wasm = check_source(source, self.wasm)
        self.assertEqual(owned, from_wasm)
        wat = emit_module(to_module(from_wasm))
        reference = self.executable(driver.compile_to_wat(source, frontend="snek"))
        if not runtime_divergence:
            self.same_execution(reference, self.python_result(source))
        # Always require exact parity with the production compiler, including
        # the shared runtime's documented float-repr/surrogate differences.
        expected = reference
        self.same_execution(self.executable(wat), expected)
        if optimize and os.environ.get("SNEK_WASM_OPT"):
            self.same_execution(self.executable(wat, True), expected)
        return owned


class BodyCases:
    def test_scalar_fixture(self):
        self.compare((ROOT / "examples/scalar_checker.py").read_text())
        self.compare((ROOT / "examples/uncaught_keyerror.py").read_text())

    def test_numeric_benchmarks(self):
        for name in ("fib", "mandelbrot"):
            with self.subTest(name=name):
                self.compare((ROOT / f"bench/programs/{name}.py").read_text())

    def test_behavior(self):
        for name, source in PROGRAMS.items():
            with self.subTest(name=name):
                self.compare(source)

    def test_runtime_errors_and_effect_order(self):
        for source in [
            'print("before")\nprint("must not print", 1 // 0)',
            'print("before")\nraise ValueError("bad")',
            'raise KeyError("key")',
            'raise KeyError()',
            'assert False, "failed"',
            'print("🐍"[1])',
            'print(ord("xx"))',
            'print(chr(0xd800))',
            'from snek import i32\nprint(i32(1e9999))',
            'def msg() -> str:\n    print("lazy message")\n    return "bad"\nassert False, msg()',
        ]:
            with self.subTest(source=source):
                self.compare(source, runtime_divergence=source == 'print(chr(0xd800))')

    def test_body_errors(self):
        for source, message in ERRORS:
            with self.subTest(source=source):
                with self.assertRaises(ValueError) as error:
                    check(source)
                self.assertIn(message, str(error.exception))
                self.assertRegex(str(error.exception), r"^line \d+, column \d+: ")
                # The original checker has historically accepted Return inside
                # a main guard; the new pass explicitly rejects that case.
                if "return outside" not in message:
                    with self.assertRaises(CompileError):
                        driver.compile_to_wat(source)
                result = runner.run_binary(self.wasm, stdin=source.encode())
                self.assertNotEqual(result.code, 0)
                self.assertEqual(result.stdout, b"")
                self.assertIn(str(error.exception), result.stderr.decode())

    def test_existing_negative_corpus_fails_closed(self):
        semantic, unported = 0, 0
        for path in sorted((ROOT / "tests/errors").glob("*.py")):
            source = path.read_text()
            with self.subTest(path=path.name):
                with self.assertRaises(ValueError) as error:
                    check(source)
                if "does not yet support" in str(error.exception):
                    unported += 1
                else:
                    semantic += 1
                result = runner.run_binary(self.wasm, stdin=source.encode())
                self.assertNotEqual(result.code, 0)
                self.assertEqual(result.stdout, b"")
                self.assertIn(str(error.exception), result.stderr.decode())
        # Keep subset rejection distinct from actual semantic-checking coverage.
        self.assertGreaterEqual(semantic, 50)
        self.assertGreater(unported, 0)

    def test_no_python_checking_or_source_ast_adapter(self):
        source = (ROOT / "examples/scalar_checker.py").read_text()
        expected = compile_source(source)
        with patch("ast.parse", side_effect=AssertionError("Python parser fallback")), \
             patch("snekc.frontend.to_ast", side_effect=AssertionError("source AST adapter fallback")), \
             patch.object(Checker, "run", side_effect=AssertionError("Python checker fallback")), \
             patch.object(Checker, "declare_module", side_effect=AssertionError("Python declaration fallback")):
            self.assertEqual(compile_source(source), expected)
            self.assertEqual(compile_source(source, self.wasm), expected)
            for unsupported in UNPORTED:
                with self.subTest(source=unsupported), self.assertRaisesRegex(ValueError, "does not yet support"):
                    compile_source(unsupported)
        for unsupported in UNPORTED:
            result = runner.run_binary(self.wasm, stdin=unsupported.encode())
            self.assertNotEqual(result.code, 0)
            self.assertEqual(result.stdout, b"")
            self.assertIn(b"does not yet support", result.stderr)

    def test_ir_types_and_conversions(self):
        source = '''from snek import i32, i64, f64
def widen(n: i32) -> i64: return n
def mix(n: i32) -> f64: return n + 0.5
def narrow(s: str | None) -> str:
    if s is None: return "none"
    return s
print(widen(1), mix(2), narrow("x"))
'''
        module = to_module(check_source(source, self.wasm))
        original = Checker(parse_source(source)).run()
        by_name = {f.name: f for f in original.funcs}
        for function in module.funcs:
            if function.name.endswith(".__new__"):
                continue
            expected = by_name[function.name]
            self.assertEqual(function.params, expected.params)
            self.assertEqual(function.ret, expected.ret)
            self.assertEqual({n: t for n, t in function.locals.items() if not n.startswith("b.")},
                             {n: t for n, t in expected.locals.items() if not n.startswith("t.")})
        from snekc import ir, types as t
        functions = {f.name: f for f in module.funcs}
        self.assertIsInstance(functions["widen"].body[0].value, ir.Convert)
        value = functions["narrow"].body[-1].value
        self.assertEqual(value.ty, t.STR)
        self.assertEqual(value.declared, t.OptionalT(t.STR))

    def test_flow_and_boundaries(self):
        # Infinite loops with only nested breaks cannot fall through. Do not run.
        for source in ["from snek import i32\ndef f() -> i32:\n    while True: pass",
                       "from snek import i32\ndef f() -> i32:\n    while True:\n        while True: break"]:
            check(source)
            driver.compile_to_wat(source)
        self.compare('''from snek import i64
def f() -> i64:
    x: i64 = -9223372036854775808
    return x
print(f(), 9223372036854775807, -0x8000000000000000)
''')
        for text in ("9223372036854775808", "-9223372036854775809"):
            with self.assertRaisesRegex(ValueError, "does not fit"):
                check(f"print({text})")

    def test_generated_arithmetic(self):
        rng = random.Random(919)
        expressions = []
        for _ in range(100):
            a, b = rng.randrange(-100, 100), rng.randrange(1, 100)
            op = rng.choice(["+", "-", "*", "//", "%", "&", "|", "^", "/"])
            expressions.append(f"print(({a}) {op} ({b}))")
        self.compare("\n".join(expressions), runtime_divergence=True)

    def test_optimized_checker(self):
        if not os.environ.get("SNEK_WASM_OPT"):
            self.skipTest("set SNEK_WASM_OPT to include optimized checker")
        optimized = self.directory / "bodies.opt.wasm"
        driver.build(self.path, optimized, optimize=True, frontend="snek")
        for source in [*(PROGRAMS.values()), (ROOT / "examples/scalar_checker.py").read_text()]:
            with self.subTest(source=source[:70]):
                self.assertEqual(check_source(source, optimized), check_source(source))
        self.assertEqual(declare(self.source).tree().get("body_checking").text, "pending")
        self.assertEqual(check_source(self.source, optimized), check_source(self.source))

    def test_ir_schema_rejections(self):
        data = check_source('print("ok")')
        broken = copy.deepcopy(data)
        broken["schema"] = 999
        with self.assertRaisesRegex(ValueError, "schema"):
            to_module(broken)
        broken = copy.deepcopy(data)
        broken["functions"][-1]["body"][0]["exprs"][0]["_type"] = "UnportedExpr"
        with self.assertRaisesRegex(ValueError, "unsupported owned IR expression"):
            to_module(broken)
        broken = copy.deepcopy(data)
        broken["declarations"]["classes"][0]["fields"][0]["name"] = "changed"
        with self.assertRaisesRegex(ValueError, "field layout"):
            to_module(broken)

    def test_cli(self):
        source, out = self.directory / "cli.py", self.directory / "cli.wasm"
        source.write_text('print("cli works")')
        command = [sys.executable, str(ROOT / "bootstrap/body_bridge.py"), str(source), "-o", str(out),
                   "--checker-wasm", str(self.wasm), "--no-opt", "--run"]
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "cli works\n")
        original = out.read_bytes()
        source.write_text("print([])")
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(out.read_bytes(), original)
        command[command.index("-o") + 1] = str(source)
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(source.read_text(), "print([])")

    def test_component_core(self):
        source = PROGRAMS["branches"]
        wat = compile_source(source, self.wasm, component=True)
        binary = self.directory / "component-core.wasm"
        binary.write_bytes(encode(wat))
        if shutil.which("wasm-tools"):
            result = subprocess.run(["wasm-tools", "validate", str(binary)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('"wasi_snapshot_preview1"', wat)


def checker_variant(name, cases, fixture=BodyFixture):
    """Compose an explicit case inventory with a seed or rebuilt checker fixture."""
    return type(name, (*cases, fixture, unittest.TestCase), {"__module__": cases[-1].__module__})


CASES = (BodyCases,)
BodyTests = checker_variant("BodyTests", CASES)


if __name__ == "__main__":
    unittest.main(verbosity=2)
