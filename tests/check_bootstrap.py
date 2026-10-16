"""Bootstrap backend parity, numeric boundaries, and a three-stage rebuild."""
from __future__ import annotations

import math
from pathlib import Path
import random
import struct
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bootstrap import encode as port
from bootstrap.bootstrap import SOURCE, assemble, bootstrap
from snekc import driver, runner
from snekc.wasm import encode
from check_wasm import EH_FIXTURE, GC_FIXTURE, MEMORY, SIMPLE


def numeric_fixture() -> str:
    rng = random.Random(7108)
    forms = []
    for width, fmt, count in ((32, "f", 300), (64, "d", 300)):
        for _ in range(count):
            value = struct.unpack("<" + fmt, rng.getrandbits(width).to_bytes(width // 8, "little"))[0]
            if math.isfinite(value):
                forms.append(f"(drop (f{width}.const {value!r}))")
                forms.append(f"(drop (f{width}.const {value.hex()}))")
        for literal in ("-0.0", "inf", "-inf", "nan", "-nan:0x123", "1.0000000596046448", "0x1.00000100000001p0",
                        "0x1p-149" if width == 32 else "0x1p-1074"):
            forms.append(f"(drop (f{width}.const {literal}))")
    for width in (32, 64):
        for value in (0, 63, 64, 127, 128, -1, -65, -(1 << (width - 1)), (1 << width) - 1):
            forms.append(f"(drop (i{width}.const {value}))")
    return "(module (func " + " ".join(forms) + "))"


TABLE_FIXTURE = '''(module
  (type $sig (func (param i32) (result i32)))
  (tag $error (param i32))
  (global $g (mut i32) (i32.const 0))
  (func $id (type $sig) (param $v i32) (result i32) (local.get $v))
  (elem declare func $id)
  (func (export "test") (result i32) (local $x i32)
    (local.set $x (i32.const 1))
    (global.set $g (local.tee $x (call $id (local.get $x))))
    (drop (call_ref $sig (local.get $x) (ref.func $id)))
    (loop $done (br_if $done (i32.const 0)))
    (block $caught (result i32)
      (try_table (catch $error $caught) (throw $error (global.get $g)))
      (i32.const 0))))'''


def saturating_fixture() -> str:
    forms = []
    for result in ("i32", "i64"):
        for source in ("f32", "f64"):
            for sign in ("s", "u"):
                forms.append(f"(drop ({result}.trunc_sat_{source}_{sign} ({source}.const 1.25)))")
    return "(module (func " + " ".join(forms) + "))"


class PortTests(unittest.TestCase):
    def test_shared_tables_preserve_all_indexed_and_saturating_opcodes(self):
        for wat in (TABLE_FIXTURE, saturating_fixture()):
            encoder = port.Encoder(port.Reader(list(wat.encode())).parse())
            tables = [encoder.primitives, encoder.index_kinds, encoder.index_codes,
                      encoder.control_codes, encoder.catch_codes, encoder.saturating]
            before = [table.copy() for table in tables]
            self.assertEqual(bytes(encoder.encode()), encode(wat, debug_names=False))
            self.assertEqual(tables, before)

    def test_source_is_accepted_by_snek(self):
        self.assertIn("$Encoder.encode", driver.compile_to_wat(SOURCE.read_text()))

    def test_emitter_preserves_float_midpoint(self):
        wat = driver.compile_to_wat("from snek import f32\ndef value() -> f32:\n    return 1.0000000596046448\n")
        self.assertIn("f32.const 0x1.0000010000000p+0", wat)
        self.assertEqual(bytes(port.encode(list(wat.encode()))), encode(wat, debug_names=False))

    def test_fixture_parity(self):
        growth = '(module (memory $m 1 2) (func (drop (memory.grow $m (i32.const 1))) (drop (memory.size))))'
        for wat in ("(module)", GC_FIXTURE, EH_FIXTURE, numeric_fixture(), growth):
            self.assertEqual(bytes(port.encode(list(wat.encode()))), encode(wat, debug_names=False))

    def test_all_numeric_and_memory_opcodes(self):
        functions = [f"(func (unreachable) (drop ({op})))" for op in SIMPLE
                     if op.startswith(("i32.", "i64.", "f32.", "f64."))]
        for op in MEMORY:
            body = f"({op} offset=65536 align=1)"
            functions.append(f"(func (unreachable) {'(drop ' + body + ')' if 'load' in op else body})")
        wat = "(module (memory 1) " + " ".join(functions) + ")"
        self.assertEqual(bytes(port.encode(list(wat.encode()))), encode(wat, debug_names=False))

    def test_float_ties_subnormals_and_sign(self):
        cases = [
            (32, "0x1.000001p0", "0000803f"),  # tie rounds to even lower significand
            (32, "0x1.000003p0", "0200803f"),  # odd lower significand rounds up
            (32, "0x1p-150", "00000000"),
            (32, "0x1.000001p-150", "01000000"),
            (64, "0x1p-1075", "0000000000000000"),
            (64, "-0x1p-1075", "0000000000000080"),
            (64, "0x1.fffffffffffffp1023", "ffffffffffffef7f"),
            (64, "-nan:0x123", "230100000000f0ff"),
        ]
        for width, literal, expected in cases:
            with self.subTest(literal=literal):
                self.assertEqual(bytes(port.float_bytes(literal, width)), bytes.fromhex(expected))

    def test_binary_strings_and_nested_comments(self):
        data = bytes(range(256))
        text = '(; (; " ;) ;) (module (data "' + ''.join(f"\\{byte:02x}" for byte in data) + '" "é\\u{1f40d}"))'
        self.assertEqual(bytes(port.encode(list(text.encode()))), encode(text, debug_names=False))

    def test_rejected_syntax(self):
        for text in ("(module (unknown))", "(module (func (bad.op)))", "(module (func (call $x)))",
                     "(module (func (br $x)))", "(module (func $f) (func $f))", "(module", '(module (data "\\q"))',
                     '(module (data (i32.const 0) "x"))', "(module (func (i32.const 4294967296)))",
                     "(module (func (i64.const -9223372036854775809)))"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                port.encode(list(text.encode()))


class CompiledTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            runner.available_runtime()
        except RuntimeError as error:
            raise unittest.SkipTest(str(error))
        cls.directory = tempfile.TemporaryDirectory(prefix="snek-bootstrap-test-")
        cls.addClassCleanup(cls.directory.cleanup)
        cls.encoder = Path(cls.directory.name, "encoder.wasm")
        cls.encoder.write_bytes(encode(driver.compile_to_wat(SOURCE.read_text()), debug_names=False))

    def test_compiled_numeric_and_gc_parity(self):
        growth = '(module (memory $m 1 2) (func (drop (memory.grow $m (i32.const 1))) (drop (memory.size))))'
        for wat in (numeric_fixture(), GC_FIXTURE, EH_FIXTURE, growth, TABLE_FIXTURE, saturating_fixture()):
            self.assertEqual(assemble(self.encoder, wat), encode(wat, debug_names=False))

    def test_compiled_errors_leave_stdout_empty(self):
        for text in (b"(module (func (bad.op)))", b"(module", b"(module (func (i64.const 18446744073709551616)))"):
            result = runner.run_binary(self.encoder, stdin=text)
            self.assertNotEqual(result.code, 0)
            self.assertEqual(result.stdout, b"")
            self.assertIn(b"ValueError:", result.stderr)

    def test_three_stage_bootstrap(self):
        manifest = bootstrap(Path(self.directory.name, "stages"))
        self.assertEqual(manifest["optimizer"], None)
        binaries = [Path(self.directory.name, "stages", name).read_bytes() for name in manifest["stages"]]
        self.assertEqual(binaries[0], binaries[1])
        self.assertEqual(binaries[1], binaries[2])
        self.assertIn("Python frontend", manifest["scope"])


if __name__ == "__main__":
    unittest.main()
