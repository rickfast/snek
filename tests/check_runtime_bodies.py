"""Runtime-helper and exception checker parity, including prior body suites."""
from __future__ import annotations

import copy
from unittest.mock import patch
import unittest

import check_aggregate_bodies as aggregates
import check_bodies as base


PROGRAMS = {
    "exception_string_override": '''class Detail(ValueError):
    def __str__(self) -> str:
        return "detail: " + super().__str__()
class MoreDetail(Detail):
    def __str__(self) -> str:
        return super().__str__() + "!"
class Lookup(KeyError):
    def __str__(self) -> str:
        return "lookup: " + super().__str__()
def main() -> None:
    print(Detail("message"), MoreDetail("message"), Lookup("key"), Lookup())
    try: raise MoreDetail("caught")
    except Exception as e: print(e)
main()
''',
    "nested_cleanup": '''from snek import i32
def f(n: i32, log: list[str]) -> i32:
    try:
        try:
            if n == 0: raise KeyError("zero")
            return 10 // n
        except LookupError as e:
            log.append(str(e))
            raise
        finally:
            log.append("inner")
    except Exception:
        return -1
    finally:
        log.append("outer")
def main() -> None:
    log: list[str] = []
    print(f(0, log), f(2, log), log)
    for i in range(4):
        try:
            if i == 0: continue
            if i == 2: break
            print(i)
        finally: print("cleanup", i)
main()
''',
    "finally_override": '''def f() -> str:
    try: return "never returned"
    finally: raise ValueError("cleanup failed")
def main() -> None:
    try: print(f())
    except ValueError as e: print(e)
    try:
        try: raise ValueError("body")
        except ValueError: raise KeyError("handler")
        finally: print("still cleaned")
    except KeyError as e: print(e)
    try:
        pass
    except ValueError: print("not reached")
    else: print("else")
    finally: print("final")
main()
''',
    "handler_scopes": '''from snek import i32
def f(flag: bool) -> str:
    try:
        if flag: raise ValueError("yes")
        result = "normal"
    except ValueError as e: result = str(e)
    finally:
        for i in range(3):
            if i == 1: break
        try: raise KeyError("inside final")
        except KeyError as nested: print(nested)
    return result
def main() -> None:
    e: BaseException | None = None
    try: raise ValueError("optional target")
    except ValueError as e: print(str(e))
    print(f(True), f(False))
main()
''',
    "dict_unwind": '''from snek import i32
def fail(d: dict[str, i32]) -> None:
    for key in d:
        raise ValueError(key)
def main() -> None:
    d = {"a": 1}
    try: fail(d)
    except ValueError as e: print(e)
    d["b"] = 2
    for key in d: print(key)
    try:
        for key in d:
            del d[key]
    except RuntimeError as e: print(e)
    d.clear()
    print(d)
main()
''',
    "string_order": '''from snek import i32
def recv() -> str:
    print("recv", end=";")
    return "a-b-c"
def sep() -> str:
    print("sep", end=";")
    return "-"
def count() -> i32:
    print("count", end=";")
    return 1
print(recv().split(maxsplit=count(), sep=sep()))
print(recv().replace(sep(), "!", count=count()))
''',
    "integer_errors": '''from snek import i32, i64
def main() -> None:
    for text in ["2147483648", "-2147483649", "9223372036854775808", "-9223372036854775809"]:
        try: print(i32(text))
        except OverflowError as e: print(e)
        try: print(i64(text))
        except OverflowError as e: print(e)
    print(i64("-9223372036854775808"), i32("۱۲۳"), i32("  -0_1 "))
main()
''',
}

ERRORS = [
    "print('x'.find(sub='x'))", "print('x'.replace(old='x', new='y'))",
    "print('x'.upper(1))", "print('x'.split(maxsplit='bad'))",
    "print('x'.split('a', sep='b'))", "print('x'.split(unknown='b'))",
    "s: str | None = None\nprint(s.upper())", "print(ValueError(None))",
    "class E: pass\ntry: pass\nexcept E: pass",
    "try: pass\nexcept 1: pass", "try: pass\nexcept ValueError as e: pass\nprint(e)",
    "from snek import i32\ne: i32 = 1\ntry: pass\nexcept ValueError as e: pass",
    "from snek import i32\nclass E(ValueError):\n    n: i32\n    def __init__(self, n: i32) -> None: self.n = n",
    "raise", "raise None", "x: Exception | None = None\nraise x",
    "def f() -> None:\n    try: pass\n    finally: return",
    "for i in range(2):\n    try: pass\n    finally: break",
    "try:\n    raise ValueError()\nexcept ValueError:\n    try: pass\n    finally: raise",
    "s: str | None = 'x'\ntry:\n    s = None\n    raise ValueError()\nexcept ValueError: print(s.upper())",
    "try: n = 1\nexcept Exception: print(n)",
    "try: n = 1\nfinally: print(n)",
    "class E(ValueError):\n    def __str__(self) -> str: return super().__str__(1)",
    "class E(ValueError):\n    def __str__(self) -> str: return super().__str__(value='x')",
]


class RuntimeTests(aggregates.AggregateTests):
    def test_runtime_fixture(self):
        self.compare((base.ROOT / "examples/runtime_checker.py").read_text())
        self.compare((base.ROOT / "examples/str_methods.py").read_text())

    def test_runtime_behavior(self):
        for name, source in PROGRAMS.items():
            with self.subTest(name=name):
                # CPython's i32/i64 shims alias int, which has no machine-width
                # overflow. Require the existing compiler's exact exceptions.
                self.compare(source, runtime_divergence=name == "integer_errors")

    def test_runtime_errors(self):
        for source in ERRORS:
            source = aggregates.local_program(source)
            with self.subTest(source=source):
                with self.assertRaises(ValueError) as error: base.check(source)
                with self.assertRaises(base.CompileError): base.driver.compile_to_wat(source)
                result = base.runner.run_binary(self.wasm, stdin=source.encode())
                self.assertNotEqual(result.code, 0)
                self.assertEqual(result.stdout, b"")
                self.assertIn(str(error.exception), result.stderr.decode())

    def test_helper_privacy_and_no_fallback(self):
        source = (base.ROOT / "examples/runtime_checker.py").read_text()
        expected = base.compile_source(source)
        with patch("ast.parse", side_effect=AssertionError("Python parser fallback")), \
             patch("snekc.frontend.to_ast", side_effect=AssertionError("AST adapter fallback")), \
             patch.object(base.Checker, "run", side_effect=AssertionError("Python checker fallback")), \
             patch.object(base.Checker, "builtin_func", side_effect=AssertionError("Python helper checker fallback")), \
             patch.object(base.Checker, "declare_module", side_effect=AssertionError("Python declaration fallback")):
            self.assertEqual(base.compile_source(source), expected)
            self.assertEqual(base.compile_source(source, self.wasm), expected)
        for source in ["print(__blen('x'))", "print(__str_upper('x'))", "print('x'.__str_upper())",
                       "def f(b: __Buf) -> None: pass"]:
            with self.subTest(source=source):
                with self.assertRaises(ValueError): base.check(source)
                result = base.runner.run_binary(self.wasm, stdin=source.encode())
                self.assertNotEqual(result.code, 0)
                self.assertEqual(result.stdout, b"")

    def test_helper_demand_and_unicode(self):
        empty = base.check_source('print("plain")', self.wasm)
        self.assertFalse(any(f["name"].startswith("__str_") for f in empty["functions"]))
        source = 'print("ΑΣ.".lower(), "ß".upper(), "٤".isdecimal())'
        data = self.compare(source)
        names = [f["name"] for f in data["functions"]]
        self.assertEqual(len(names), len(set(names)))
        self.assertIn("__str_lower", names)
        self.assertIn("__sigma", names)
        self.assertNotIn("__str_split", names)
        self.assertIn(0, data["unicode_cases"])
        self.assertIn(1, data["unicode_cases"])
        self.assertNotEqual(data["unicode_flags"], 0)
        decimal = base.check_source('from snek import i32\nprint(i32("٤٢"))', self.wasm)
        self.assertTrue(decimal["unicode_decimal"])
        broken = copy.deepcopy(data)
        broken["runtime_abi"] = "old runtime"
        with self.assertRaisesRegex(ValueError, "runtime ABI"): base.to_module(broken)

    def test_optimized_runtime_checker(self):
        if not base.os.environ.get("SNEK_WASM_OPT"):
            self.skipTest("set SNEK_WASM_OPT to include optimized checker")
        optimized = self.directory / "runtime.opt.wasm"
        base.driver.build(self.path, optimized, optimize=True, frontend="snek")
        for source in [*PROGRAMS.values(), (base.ROOT / "examples/runtime_checker.py").read_text(),
                       (base.ROOT / "examples/str_methods.py").read_text()]:
            self.assertEqual(base.check_source(source, optimized), base.check_source(source))

    def test_runtime_component_core(self):
        source = (base.ROOT / "examples/runtime_checker.py").read_text()
        wat = base.compile_source(source, self.wasm, component=True)
        binary = self.directory / "runtime.component-core.wasm"
        binary.write_bytes(base.encode(wat))
        self.assertNotIn('"wasi_snapshot_preview1"', wat)
        if base.shutil.which("wasm-tools"):
            result = base.subprocess.run(["wasm-tools", "validate", str(binary)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
