"""Snek-owned ordered builtin checking, including the prior body suites."""
from __future__ import annotations

import random
import unittest
from unittest.mock import patch

import check_runtime_bodies as runtime
import check_aggregate_bodies as aggregates
import check_bodies as base


PROGRAMS = {
    "contextual_empty": '''from snek import i32, i64, f32, f64
def empty() -> list[str]: return sorted([], reverse=True, key=None)
def wide() -> i64: return sum([])
def small() -> f32: return min([], default=1.5)
def text() -> str: return max([], default="text")
def take(xs: list[i64]) -> i64: return sum(xs)
def main() -> None:
    xs: list[i64] = sorted([])
    print(empty(), wide(), small(), text(), xs, take(sorted([])))
    print(sum([], start=i64(9)), sum([], 0.5), min([], default=True))
    print(min("z", "a", key=None), max(False, True, key=None))
    for flag in [False, True]:
        try:
            if flag: print(min([]))
            else: print(max([]))
        except ValueError as e: print(e)
main()
''',
    "optional_and_identity": '''from snek import i32
def optional(flag: bool) -> str | None:
    return "fallback" if flag else None
def main() -> None:
    first = "same".replace("s", "s", 1)
    second = "sa" + "me"
    words = [first, second]
    print(min(words) is first, max(words) is first)
    print(sorted(words)[0] is first, sorted(words, reverse=True)[0] is first)
    maybe: list[str] | None = words
    if maybe is not None:
        print(sorted(maybe), min(maybe), max(maybe))
    none: list[str] = []
    value: str | None = min(none, default=optional(False))
    print(value, max(none, default=optional(True)))
    value = min(words, default=None)
    if value is not None: print(value.upper())
main()
''',
    "argument_effects": '''from snek import i32
def source(xs: list[i32], log: list[str]) -> list[i32]:
    log.append("source")
    return xs
def reverse(xs: list[i32], log: list[str]) -> bool:
    log.append("reverse")
    xs.append(0)
    return True
def fallback(xs: list[i32], log: list[str]) -> i32:
    log.append("default")
    xs.clear()
    return 42
def start(xs: list[i32], log: list[str]) -> i32:
    log.append("start")
    xs.append(3)
    return 7
def fail(log: list[str]) -> list[i32]:
    log.append("fail")
    raise ValueError("source failed")
def value(n: i32, log: list[str]) -> i32:
    log.append(str(n))
    return n
def main() -> None:
    log: list[str] = []
    xs = [3, 1, 2]
    print(sorted(source(xs, log), key=None, reverse=reverse(xs, log)), xs, log)
    print(min(source(xs, log), key=None, default=fallback(xs, log)), xs, log)
    print(sum(source(xs, log), start=start(xs, log)), xs, log)
    print(max(value(9, log), value(8, log), key=None), log)
    try: print(sorted(fail(log), reverse=reverse(xs, log)))
    except ValueError as e: print(e, log)
    try: print(sum(fail(log), start=start(xs, log)))
    except ValueError as e: print(e, log)
main()
''',
    "numeric_runtime": '''from snek import i32, i64, f32, f64
def main() -> None:
    print(sum([2147483647, 1]), sum([i64(9223372036854775807), i64(1)]))
    print(sum([True, False], 0.5), sum([i32(9)], i64(2147483648)))
    floats: list[f32] = [0.25, -0.5, 0.75]
    print(sum(floats, 0.0), sum([1e16, 1.0, -1e16]), sum([1.0], 1 if True else 2.0))
    print(sum([], -0.0), sum([0.0], -0.0))
    nan = 1e999 - 1e999
    print(sorted([3.0, nan, 2.0, 1.0]), sorted([3.0, nan, 2.0, 1.0], reverse=True))
    print(min([nan, 1.0]), max([1.0, nan]), sum([1e999, -1e999], 0.0))
main()
''',
}

ERRORS = [
    "print(sorted())", "print(sorted([1], False))", "print(sorted([1], reverse=1))",
    "print(sorted([1], unknown=True))", "print(sorted([1], reverse=True, reverse=False))",
    "print(sorted([1], **{}))", "print(sorted('abc'))", "print(sorted((1, 2)))",
    "print(sorted({'x': 1}))", "print(sorted([]))", "print(sorted([[1]]))",
    "from snek import i32\nx: list[i32] | None = None\nprint(sorted(x))",
    "x: list[str | None] = ['x', None]\nprint(min(x))",
    "print(min([1], default=None))", "print(max([1], default=1.5))",
    "print(min([1.0], default=1))", "print(max([1.0], default=1 if True else 2.0))",
    "print(min([], default=None))", "print(max([], default=[]))",
    "print(min([1], reverse=True))", "print(max([1], default=1, default=2))",
    "print(min(1, 2, default=0))", "print(min(1, 2, key=None, default=0))",
    "print(max(1, 2, 3, key=None))", "print(min(1, 1.5, key=None))",
    "print(sum())", "print(sum([1], 2, 3))", "print(sum([1], 2, start=3))",
    "print(sum([1], start=2, start=3))", "print(sum([1], key=None))",
    "print(sum([1], **{}))", "print(sum((1, 2)))", "print(sum('abc'))",
    "print(sum(['x']))", "print(sum([1], True))", "print(sum([1], None))",
    "print(sum([1], start=1 if True else 2.0))", "print(sum([], start=True))",
    "from snek import i64\nprint(sum([i64(1)], 0.5))",
    "from snek import i64\nprint(sum([1.0], i64(2)))",
    "from snek import f64\nxs: list[f64] = []\nprint(sum(xs))",
    "from snek import f64\nxs: list[f64] = [1.0]\nprint(sum(xs, 0))",
    "from snek import f64\ndef f() -> f64: return sum([])",
    "from snek import f64\ndef f() -> f64: return sum([1])",
    "from snek import i32\nxs: list[i32] | None = None\nprint(sum(xs))",
    "def f() -> None:\n    print(sorted([1]))\n    sorted = 3",
]

CALLABLE_KEYS = [
    'print(sorted(["abc", "x"], key=lambda s: len(s)))',
    'from snek import i32\ndef key(x: i32) -> i32: return -x\nprint(min([1, 2], key=key))',
    'from snek import i32\ndef key(x: i32) -> i32: return x\nprint(max(1, 2, key=key))',
]


def expression_records(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from expression_records(child)
    elif isinstance(value, list):
        for child in value:
            yield from expression_records(child)


class OrderedTests(runtime.RuntimeTests):
    def test_ordered_fixture(self):
        self.compare((base.ROOT / "examples/list_builtins.py").read_text())

    def test_ordered_behavior(self):
        for name, source in PROGRAMS.items():
            with self.subTest(name=name):
                # Machine overflow and NaN sorting are existing runtime choices,
                # not a new promise of CPython parity.
                self.compare(source, runtime_divergence=name == "numeric_runtime")

    def test_ordered_errors_and_callable_keys(self):
        for source in ERRORS:
            source = aggregates.local_program(source)
            with self.subTest(source=source):
                with self.assertRaises(ValueError) as error: base.check(source)
                with self.assertRaises(base.CompileError): base.driver.compile_to_wat(source)
                result = base.runner.run_binary(self.wasm, stdin=source.encode())
                self.assertNotEqual(result.code, 0)
                self.assertEqual(result.stdout, b"")
                self.assertIn(str(error.exception), result.stderr.decode())
        for source in CALLABLE_KEYS:
            with self.subTest(source=source):
                self.compare(source)

    def test_ordered_generated_and_benchmark(self):
        rng = random.Random(3701)
        statements = ["from snek import i32", "def main() -> None:"]
        for size in (0, 1, 2, 3, 7, 8, 9, 31, 32, 33, 127, 129):
            values = [rng.randrange(-20, 21) for _ in range(size)]
            statements.extend([
                f"    xs: list[i32] = {values!r}",
                "    print(sorted(xs), sorted(xs, reverse=True), xs)",
                "    print(sum(xs), min(xs, default=99), max(xs, default=-99))",
            ])
        self.compare("\n".join([*statements, "main()\n"]))
        self.compare((base.ROOT / "bench/programs/list_builtins.py").read_text())

    def test_ordered_ir_types_and_no_fallback(self):
        source = (base.ROOT / "examples/list_builtins.py").read_text()
        expected = base.compile_source(source)
        with patch("ast.parse", side_effect=AssertionError("Python parser fallback")), \
             patch("snekc.frontend.to_ast", side_effect=AssertionError("AST adapter fallback")), \
             patch.object(base.Checker, "run", side_effect=AssertionError("Python checker fallback")), \
             patch.object(base.Checker, "check_list_builtin", side_effect=AssertionError("list checker fallback")), \
             patch.object(base.Checker, "check_ordered_builtin", side_effect=AssertionError("ordered checker fallback")), \
             patch.object(base.Checker, "declare_module", side_effect=AssertionError("declaration fallback")):
            self.assertEqual(base.compile_source(source), expected)
            self.assertEqual(base.compile_source(source, self.wasm), expected)
        data = base.check_source(source, self.wasm)
        self.assertEqual((data["schema"], data["subset"]), (7, "language-v7"))
        ops = {item["text"]: item for item in expression_records(data) if item.get("_type") == "ListOp"}
        for op, kind in [("sum_i32", "i32"), ("sum_i64", "i64"), ("sum_f64", "f64"),
                         ("min_optional", "optional"), ("max_optional", "optional"), ("sorted", "list")]:
            self.assertEqual(ops[op]["type"]["kind"], kind)
        self.assertFalse(any(item.get("_type") == "KeyedListOp" for item in expression_records(data)))

    def test_optimized_ordered_checker(self):
        if not base.os.environ.get("SNEK_WASM_OPT"):
            self.skipTest("set SNEK_WASM_OPT to include optimized checker")
        optimized = self.directory / "ordered.opt.wasm"
        base.driver.build(self.path, optimized, optimize=True, frontend="snek")
        for source in [*PROGRAMS.values(), (base.ROOT / "examples/list_builtins.py").read_text()]:
            self.assertEqual(base.check_source(source, optimized), base.check_source(source))

    def test_ordered_component_core(self):
        source = (base.ROOT / "examples/list_builtins.py").read_text()
        wat = base.compile_source(source, self.wasm, component=True)
        binary = self.directory / "ordered.component-core.wasm"
        binary.write_bytes(base.encode(wat))
        self.assertNotIn('"wasi_snapshot_preview1"', wat)
        if base.shutil.which("wasm-tools"):
            result = base.subprocess.run(["wasm-tools", "validate", str(binary)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_own_source_progress(self):
        self.assertEqual(base.check_source(self.source, self.wasm), base.check_source(self.source))

    def test_checker_roundtrip(self):
        # Check the unmodified source and run its emitted checker on fresh input.
        # The bridge/emitter are still Python, so this is not a full compiler.
        from bootstrap.build_bodies import SMOKE_SOURCE
        data = base.check_source(self.source, self.wasm)
        self.assertEqual(data, base.check_source(self.source))
        binary = self.directory / "rebuilt-checker.wasm"
        binary.write_bytes(base.encode(base.emit_module(base.to_module(data))))
        if base.shutil.which("wasm-tools"):
            validation = base.subprocess.run(["wasm-tools", "validate", str(binary)], capture_output=True, text=True)
            self.assertEqual(validation.returncode, 0, validation.stderr)
        result = base.runner.run_binary(binary, stdin=SMOKE_SOURCE.encode())
        self.assertEqual(result.code, 0, result.stderr.decode())
        self.assertEqual(result.stderr, b"")
        self.assertEqual(base.json.loads(result.stdout), base.check_source(SMOKE_SOURCE))


if __name__ == "__main__":
    unittest.main(verbosity=2)
