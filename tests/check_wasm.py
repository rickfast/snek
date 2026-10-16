"""Native encoder regressions. No external tools required for the unit tests.

When wasm-tools is on PATH, also validate focused fixtures and compare numeric
instruction encodings byte-for-byte with its independent WAT assembler.
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from snekc import driver
from snekc.emit_wat import wat_string
from snekc.wasm import EncodeError, Instructions, MEMORY, Module, SIMPLE, constant, encode, parse, sleb, uleb


def unsigned(data: bytes, pos: int = 0):
    value, shift = 0, 0
    while True:
        byte = data[pos]
        pos += 1
        value |= (byte & 127) << shift
        if byte < 128:
            return value, pos
        shift += 7


def sections(binary: bytes):
    assert binary[:8] == b"\0asm\x01\0\0\0"
    pos = 8
    while pos < len(binary):
        number = binary[pos]
        size, pos = unsigned(binary, pos + 1)
        yield number, binary[pos:pos + size]
        pos += size


def instruction(text: str, declarations: str = "") -> bytes:
    module = Module(f"(module {declarations})")
    module.collect()
    return Instructions(module, labels=[None]).instruction(parse(f"(module {text})")[1])


GC_FIXTURE = """(module
  (rec
    (type $base (sub (struct (field $value (mut i32)) (field (ref null $child)))))
    (type $child (sub final $base (struct (field $value (mut i32)) (field (ref null $child)))))
    (type $bytes (array (mut i8)))
    (type $sig (func (param (ref $base)) (result i32))))
  (tag $exception (param (ref $base)))
  (data $data "\\00\\ff")
  (global $fn (ref $sig) (ref.func $get))
  (func $get (type $sig) (param $obj (ref $base)) (result i32)
    (struct.get $base $value (local.get $obj)))
  (func $test (export "test") (result i32)
    (local $obj (ref $base))
    (local.set $obj (struct.new $child (i32.const 42) (ref.null $child)))
    (drop (array.get_u $bytes (array.new_data $bytes $data (i32.const 0) (i32.const 2)) (i32.const 1)))
    (drop (ref.test (ref $child) (local.get $obj)))
    (drop (ref.cast (ref null $child) (local.get $obj)))
    (call_ref $sig (local.get $obj) (global.get $fn))))"""

EH_FIXTURE = """(module
  (tag $error (param i64))
  (func (export "test") (result i64)
    (block $caught (result i64)
      (try_table (catch $error $caught)
        (if (i32.const 1) (then (throw $error (i64.const 42))))
        (br $caught (i64.const 0)))
      (unreachable))))"""


class EncodingTests(unittest.TestCase):
    def test_memory_size_and_growth_encoding(self):
        self.assertEqual(instruction('(memory.size)', '(memory $m 1)'), b'\x3f\x00')
        self.assertEqual(instruction('(memory.grow $m (i32.const 1))', '(memory $m 1)'), b'\x41\x01\x40\x00')
        with self.assertRaises(EncodeError):
            instruction('(memory.size $missing)', '(memory 1)')

    def test_empty_module(self):
        self.assertEqual(encode("(module)"), b"\0asm\x01\0\0\0")

    def test_unsigned_leb_boundaries(self):
        for number, want in ((0, "00"), (127, "7f"), (128, "8001"), (16383, "ff7f"),
                             (16384, "808001"), (0xffffffff, "ffffffff0f")):
            self.assertEqual(uleb(number), bytes.fromhex(want))
        for invalid in (-1, 1 << 32):
            with self.assertRaises(EncodeError):
                uleb(invalid)

    def test_signed_leb_boundaries(self):
        for number, want in ((-65, "bf7f"), (-64, "40"), (-1, "7f"), (0, "00"),
                             (63, "3f"), (64, "c000"), (127, "ff00"), (-128, "807f"),
                             (-2147483648, "8080808078"), (2147483647, "ffffffff07"),
                             (-9223372036854775808, "8080808080808080807f")):
            self.assertEqual(sleb(number), bytes.fromhex(want))

    def test_integer_constants_accept_unsigned_bit_patterns(self):
        for bits in (32, 64):
            op = f"i{bits}.const"
            self.assertEqual(constant(op, str((1 << bits) - 1)), constant(op, "-1"))
            self.assertEqual(constant(op, hex(1 << (bits - 1))), constant(op, str(-(1 << (bits - 1)))))
            for invalid in (str(1 << bits), str(-(1 << (bits - 1)) - 1)):
                with self.assertRaises(EncodeError):
                    constant(op, invalid)

    def test_float_bits(self):
        self.assertEqual(constant("f32.const", "-0.0"), b"\x43" + bytes.fromhex("00000080"))
        self.assertEqual(constant("f64.const", "-0x0p+0"), b"\x44" + bytes.fromhex("0000000000000080"))
        self.assertEqual(constant("f64.const", "inf"), b"\x44" + struct.pack("<d", float("inf")))
        self.assertEqual(constant("f32.const", "-nan:0x123"), bytes.fromhex("43230180ff"))
        self.assertEqual(constant("f64.const", "nan"), bytes.fromhex("44000000000000f87f"))
        self.assertEqual(constant("f64.const", "0x1.234p+5"), b"\x44" + struct.pack("<d", float.fromhex("0x1.234p+5")))
        # These decimal/hex values round to an f64 halfway point but lie just
        # above the f32 tie. Going through Python float first rounds incorrectly.
        for literal in ("1.0000000596046448", "0x1.00000100000001p0"):
            self.assertEqual(constant("f32.const", literal), bytes.fromhex("43 0100803f"))
        for invalid in ("1/2", "++1", "--1"):
            with self.assertRaises(EncodeError):
                constant("f32.const", invalid)

    def test_strings_round_trip_every_byte(self):
        data = bytes(range(256))
        self.assertEqual(parse(f"(module (data {wat_string(data)}))")[1][1], data)
        self.assertEqual(parse(r'''(module (data "é\u{1f40d}\n\t\r\"\\"))''')[1][1], 'é🐍\n\t\r"\\'.encode())

    def test_comments_and_string_delimiters(self):
        self.assertEqual(parse('''(; outer (; " ) nested ;) ;) (module ;; ignored )
            (data "(; ;; )"))''')[1][1], b"(; ;; )")
        for invalid in ('(module', '(module))', '(; never ends', '(module) (module)',
                        '(module (data "unterminated))', r'(module (data "\z"))'):
            with self.subTest(invalid=invalid), self.assertRaises(EncodeError):
                encode(invalid)

    def test_types_and_recursive_forward_references(self):
        module = Module(GC_FIXTURE)
        module.collect()
        self.assertEqual(module.index("type", "$child"), 1)
        self.assertEqual(module.fields[0].get("$value"), 0)
        self.assertEqual(module.type_entries[0][:4], b"\x4e\x04\x50\x00")
        self.assertEqual(module.func_types[id(module.forms[-2])], 3)

    def test_heap_indices_use_signed_leb(self):
        types = " ".join(f"(type $t{i} (struct))" for i in range(129))
        self.assertEqual(instruction("(ref.null $t64)", types), b"\xd0\xc0\x00")
        self.assertEqual(instruction("(struct.new $t128)", types), b"\xfb\x00\x80\x01")

    def test_imports_precede_definitions_in_index_spaces(self):
        module = Module('''(module (func $defined) (global $defined i32 (i32.const 0))
            (import "host" "f" (func $imported)) (import "host" "g" (global $imported i32)))''')
        module.collect()
        for kind in ("func", "global"):
            self.assertEqual(module.index(kind, "$imported"), 0)
            self.assertEqual(module.index(kind, "$defined"), 1)

    def test_implicit_signatures_do_not_reuse_rec_group_types(self):
        module = Module('''(module (rec (type $f (func (param i32))))
            (func $explicit (type $f) (param i32))
            (func $implicit (param i32)) (func $same (param i32)))''')
        module.collect()
        self.assertEqual([module.func_types[id(f)] for f in module.forms[1:]], [0, 1, 1])

    def test_local_groups_keep_parameter_and_local_order(self):
        binary = encode('''(module (func (param $a i32) (param $b i64)
            (local $c i32) (local $d i32) (local $e f64) (drop (local.get $e))))''')
        code = dict(sections(binary))[10]
        self.assertEqual(code, bytes.fromhex("01 09 02 02 7f 01 7c 20 04 1a 0b"))

    def test_type_only_function_use_supplies_parameters(self):
        code = dict(sections(encode('''(module (type $f (func (param i32) (result i32)))
            (func (type $f) (local i64) (local.get 0)))''')))[10]
        self.assertIn(b"\x20\x00", code)

    def test_catch_depth_excludes_try_table(self):
        body = instruction('''(block $outer (block $inner
            (try_table (catch $error $inner) (br $outer))))''', "(tag $error)")
        self.assertEqual(body, bytes.fromhex("02 40 02 40 1f 40 01 00 00 00 0c 02 0b 0b 0b"))

    def test_if_condition_and_arms_have_different_label_scopes(self):
        body = instruction('''(block $out (if $inner (br $out)
            (then (br $out)) (else (br $inner))))''')
        self.assertEqual(body, bytes.fromhex("02 40 0c 00 04 40 0c 01 05 0c 00 0b 0b"))

    def test_shadowed_labels(self):
        self.assertEqual(instruction("(block $x (loop $x (br $x)))"), bytes.fromhex("02 40 03 40 0c 00 0b 0b"))

    def test_data_count_and_tag_section_order(self):
        ids = [i for i, _ in sections(encode(GC_FIXTURE))]
        self.assertEqual(ids, [1, 3, 13, 6, 7, 12, 10, 11, 0])
        self.assertEqual(dict(sections(encode(GC_FIXTURE)))[11], bytes.fromhex("01 01 02 00 ff"))

    def test_memarg(self):
        self.assertEqual(instruction("(i32.load offset=65536 align=1 (i32.const 0))"), bytes.fromhex("41 00 28 00 80 80 04"))
        for arg in ("align=0", "align=3", "align=8", "offset=-1", "align=1 align=2"):
            with self.assertRaises(EncodeError):
                instruction(f"(i32.load {arg} (i32.const 0))")

    def test_name_subsections_are_ordered(self):
        names = dict(sections(encode(GC_FIXTURE)))[0]
        self.assertTrue(names.startswith(b"\x04name"))
        ids = [i for i, _ in sections(b"\0asm\x01\0\0\0" + names[5:])]
        self.assertEqual(ids, sorted(ids))

    def test_deterministic(self):
        self.assertEqual(encode(GC_FIXTURE), encode(GC_FIXTURE))

    def test_omitting_debug_names_preserves_all_core_sections(self):
        named = [(i, s) for i, s in sections(encode(GC_FIXTURE)) if i != 0]
        raw = list(sections(encode(GC_FIXTURE, debug_names=False)))
        self.assertEqual(named, raw)

    def test_reject_unknown_and_malformed_forms(self):
        for wat, message in (
            ("(module (table 1 funcref))", "unsupported module field"),
            ("(module (func (future.op)))", "unsupported instruction future.op at WAT line 1"),
            ("(module (func (call $missing)))", "unknown func"),
            ("(module (func (br $missing)))", "unknown label"),
            ("(module (func (local.get 0)))", "local index out of range"),
            ("(module (func $x) (func $x))", "duplicate func"),
            ('(module (func (export "x")) (func (export "x")))', "duplicate export"),
            ("(module (type $f (func (param i32))) (func (type $f) (param i64)))", "inline signature"),
            ("(module (type $x (struct)) (func (type $x)))", "function type"),
            ('(module (data (i32.const 0) "x"))', "only passive data"),
        ):
            with self.subTest(wat=wat), self.assertRaisesRegex(EncodeError, message):
                encode(wat)


class PipelineTests(unittest.TestCase):
    def test_no_opt_build_never_finds_or_runs_tools(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d, "hello.wasm")
            with patch.object(driver, "find_wasm_opt", side_effect=AssertionError("Binaryen lookup")), \
                 patch.object(driver.subprocess, "run", side_effect=AssertionError("external tool")):
                result = driver.build(ROOT / "examples/hello.py", out, optimize=False, keep_wat=True)
            self.assertEqual(out.read_bytes(), encode(result.wat))
            self.assertEqual(out.with_suffix(".wat").read_text(), result.wat)

    def test_optimizer_receives_binary_not_text(self):
        seen = []

        def optimize(cmd, **kwargs):
            seen.append(cmd)
            binary = Path(cmd[1]).read_bytes()
            self.assertTrue(binary.startswith(b"\0asm"))
            Path(cmd[cmd.index("-o") + 1]).write_bytes(binary)
            return subprocess.CompletedProcess(cmd, 0, "", "")

        with tempfile.TemporaryDirectory() as d:
            with patch.object(driver, "find_wasm_opt", return_value=["fake-wasm-opt"]), \
                 patch.object(driver.subprocess, "run", side_effect=optimize):
                driver.build(ROOT / "examples/hello.py", Path(d, "out.wasm"))
        self.assertEqual(len(seen), 1)
        self.assertIn("-O3", seen[0])
        self.assertIn("--closed-world", seen[0])

    def test_failed_encoding_preserves_outputs(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d, "out.wasm")
            out.write_bytes(b"existing binary")
            out.with_suffix(".wat").write_text("existing WAT")
            with patch.object(driver, "compile_to_wat", return_value="(module (func (unknown)))"):
                with self.assertRaisesRegex(driver.ToolError, "native Wasm encoding failed"):
                    driver.build(ROOT / "examples/hello.py", out, optimize=False, keep_wat=True)
            self.assertEqual(out.read_bytes(), b"existing binary")
            self.assertEqual(out.with_suffix(".wat").read_text(), "existing WAT")

    def test_missing_optimizer_suggests_no_opt_and_preserves_output(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d, "out.wasm")
            out.write_bytes(b"existing")
            with patch.dict(os.environ, {}, clear=True), patch.object(driver.shutil, "which", return_value=None):
                with self.assertRaisesRegex(driver.ToolError, "use --no-opt"):
                    driver.build(ROOT / "examples/hello.py", out)
            self.assertEqual(out.read_bytes(), b"existing")


@unittest.skipUnless(shutil.which("wasm-tools"), "wasm-tools not installed (optional independent oracle)")
class OracleTests(unittest.TestCase):
    def compare(self, wat: str, exact: bool = False):
        with tempfile.TemporaryDirectory() as d:
            native, reference, source = (Path(d, f) for f in ("native.wasm", "reference.wasm", "source.wat"))
            native.write_bytes(encode(wat))
            source.write_text(wat)
            for cmd in (("wasm-tools", "parse", str(source), "-o", str(reference)),
                        ("wasm-tools", "validate", str(native)), ("wasm-tools", "validate", str(reference))):
                proc = subprocess.run(cmd, capture_output=True, text=True)
                self.assertEqual(proc.returncode, 0, proc.stderr)
            if exact:
                self.assertEqual([(i, s) for i, s in sections(native.read_bytes()) if i],
                                 [(i, s) for i, s in sections(reference.read_bytes()) if i])

    def test_all_numeric_opcodes_and_constants(self):
        ops = [op for op in SIMPLE if op.startswith(("i32.", "i64.", "f32.", "f64."))]
        funcs = [f"(func (unreachable) (drop ({op})))" for op in ops]
        for op, values in (("i32.const", ("-2147483648", "0xffffffff", "64", "-65")),
                           ("i64.const", ("-9223372036854775808", "0xffffffffffffffff", "9223372036854775807")),
                           ("f32.const", ("-0.0", "nan", "-nan:0x123", "inf", "0x1.234p+5",
                                          "1.0000000596046448", "0x1.00000100000001p0")),
                           ("f64.const", ("-0.0", "nan", "nan:0x123456789", "-inf", "0x1.fffffffffffffp+1023"))):
            funcs.extend(f"(func (drop ({op} {value})))" for value in values)
        self.compare("(module " + " ".join(funcs) + ")", exact=True)

    def test_all_memory_opcodes(self):
        funcs = []
        for op in MEMORY:
            body = f"({op} offset=65536 align=1)"
            if "load" in op:
                body = f"(drop {body})"
            funcs.append(f"(func (unreachable) {body})")
        self.compare("(module (memory 1) " + " ".join(funcs) + ")", exact=True)

    def test_gc_and_typed_function_refs(self):
        self.compare(GC_FIXTURE)

    def test_exception_targets(self):
        self.compare(EH_FIXTURE)

    def test_multivalue_block_type_and_type_only_function(self):
        self.compare('''(module
            (type $f (func (param i32) (result i32)))
            (func (type $f) (local.get 0))
            (func (result i32 i64) (block (result i32 i64) (i32.const 1) (i64.const 2))))''')


if __name__ == "__main__":
    unittest.main()
