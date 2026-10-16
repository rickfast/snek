"""Rebuilt Snek checker regressions and core byte I/O; emission remains Python."""
from __future__ import annotations

import hashlib
from unittest.mock import patch
import unittest

import check_bodies as base
import check_byte_io as byte_io
import check_ordered_bodies as ordered
from bootstrap.bootstrap_checker import bootstrap, rebuild


ORDER = '''from snek import read_bytes, write_bytes
print("before")
write_bytes(read_bytes())
print("after")
try:
    write_bytes([88, 256])
except ValueError as error:
    print(str(error))
try:
    write_bytes([-1])
except ValueError:
    print("negative")
write_bytes([])
'''

ERRORS = [
    "read_bytes(1)", "read_bytes(value=1)", "write_bytes()", "write_bytes([1], [2])",
    "write_bytes(data=[1])", "write_bytes([True])", "write_bytes([1.0])", "write_bytes('x')",
    "from snek import i64\nvalues: list[i64] = [1]\nwrite_bytes(values)",
    "from snek import i32\nvalues: list[i32] | None = None\nwrite_bytes(values)",
    "def f() -> None:\n    read_bytes()\n    read_bytes = 1",
]


class SelfCheckTests(ordered.OrderedTests):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.seed = cls.wasm
        # The production compiler is allowed only to create the seed above.
        with patch("ast.parse", side_effect=AssertionError("Python parser fallback")), \
             patch("snekc.frontend.to_ast", side_effect=AssertionError("AST adapter fallback")), \
             patch.object(base.Checker, "run", side_effect=AssertionError("Python checker fallback")), \
             patch.object(base.Checker, "declare_module", side_effect=AssertionError("Python declaration fallback")):
            cls.manifest = rebuild(cls.source, cls.seed, cls.directory)
        cls.stage2 = cls.directory / "checker.stage2.wasm"
        cls.stage3 = cls.directory / "checker.stage3.wasm"
        # All inherited semantic/IR/error tests exercise the rebuilt stage 3,
        # not just the original Python-checked seed.
        cls.wasm = cls.stage3

    def binary(self, source, name, optimize=False):
        binary = self.directory / (name + ".wasm")
        wat = self.directory / (name + ".wat")
        wat.write_text(base.compile_source(source, self.wasm))
        base.driver.assemble(wat, binary, optimize=optimize)
        return binary

    def test_self_rebuild_fixed_point(self):
        self.assertEqual(self.stage2.read_bytes(), self.stage3.read_bytes())
        self.assertEqual(self.manifest["source_sha256"], hashlib.sha256(self.source.encode()).hexdigest())
        for index, path in enumerate((self.seed, self.stage2, self.stage3)):
            record = self.manifest["stages"][index]
            self.assertEqual(record["sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
            self.assertEqual(record["bytes"], path.stat().st_size)
            if base.shutil.which("wasm-tools"):
                result = base.subprocess.run(["wasm-tools", "validate", str(path)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.manifest["stage2_equals_stage3"])
        self.assertTrue(self.manifest["self_ir_parity"])
        self.assertEqual(self.manifest["smoke_output"], "55")
        self.assertIsNone(self.manifest["optimizer"])

    def test_binary_roundtrip_and_eof(self):
        source = byte_io.COPY + 'write_bytes(read_bytes())\n'
        raw = self.binary(source, "copy")
        binaries = [raw]
        if base.os.environ.get("SNEK_WASM_OPT"):
            binaries.append(self.binary(source, "copy-opt", True))
        for binary in binaries:
            for data in (b"", b"\xff\x00", bytes(range(256)) * 513):
                result = base.runner.run_binary(binary, stdin=data)
                self.assertEqual((result.code, result.stdout, result.stderr), (0, data, b""))

    def test_binary_order_and_failures(self):
        binary = self.binary(ORDER, "byte-order")
        result = base.runner.run_binary(binary, stdin=b"\xff\x00")
        self.assertEqual((result.code, result.stdout, result.stderr),
                         (0, b"before\n\xff\x00after\nbyte values must be in range 0..255\nnegative\n", b""))
        # Reuse the existing custom WASI host harness for short reads/writes,
        # failed calls, zero-byte writes and impossible returned lengths.
        probe = byte_io.ByteIOTests("test_partial_host_io_and_errors")
        probe.binary = self.binary(byte_io.COPY, "host-copy")
        probe.test_partial_host_io_and_errors()

    def test_binary_cli_transport(self):
        source = self.directory / "byte-cli.py"
        source.write_text(byte_io.COPY)
        data = bytes(range(256)) * 300
        for checker in ([], ["--checker-wasm", str(self.wasm)]):
            result = base.subprocess.run([base.sys.executable, str(base.ROOT / "bootstrap/body_bridge.py"),
                                          str(source), "-o", str(self.directory / "byte-cli.wasm"),
                                          *checker, "--no-opt", "--run"], input=data, capture_output=True)
            self.assertEqual((result.returncode, result.stdout, result.stderr), (0, data, b""))

    def test_binary_signature_import_and_target(self):
        sources = ["from snek import read_bytes, write_bytes\n" + source for source in ERRORS]
        sources += ["read_bytes()", "write_bytes([1])",
                    "from snek import read_bytes\ndef read_bytes() -> None: pass",
                    "def write_bytes() -> None: pass\nfrom snek import write_bytes"]
        for source in sources:
            with self.subTest(source=source):
                with self.assertRaises(ValueError) as error: base.check(source)
                with self.assertRaises(base.CompileError): base.driver.compile_to_wat(source)
                result = base.runner.run_binary(self.wasm, stdin=source.encode())
                self.assertNotEqual(result.code, 0)
                self.assertEqual(result.stdout, b"")
                self.assertIn(str(error.exception), result.stderr.decode())
        # Unimported names are ordinary user declarations, not magic builtins.
        self.compare('def read_bytes() -> str: return "user"\nprint(read_bytes())')
        self.compare('def write_bytes(s: str) -> str: return s\nprint(write_bytes("user"))')
        with self.assertRaisesRegex(base.CompileError, "core WASI target"):
            base.compile_source(byte_io.COPY, self.wasm, component=True)

    def test_optimized_rebuilt_checker(self):
        if not base.os.environ.get("SNEK_WASM_OPT"):
            self.skipTest("set SNEK_WASM_OPT to include optimized checker")
        optimized = self.directory / "stage3.opt.wasm"
        result = base.subprocess.run([*base.driver.find_wasm_opt(), str(self.stage3), "-o", str(optimized),
                                      *base.driver.WASM_FEATURES, "-O3", "--closed-world"],
                                     capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        for source in (self.source, byte_io.COPY, ORDER, *ordered.PROGRAMS.values()):
            self.assertEqual(base.check_source(source, optimized), base.check_source(source))
        for source in base.UNPORTED:
            result = base.runner.run_binary(optimized, stdin=source.encode())
            self.assertNotEqual(result.code, 0)
            self.assertEqual(result.stdout, b"")

    def test_rebuilt_checker_arbitrary_input(self):
        for source in ('', 'print("fresh input 🐍")', 'print(sum([3, 1, 4]))'):
            self.compare(source)
        for data in (b"\xff", b"\xc0\x80", b"\xf4\x90\x80\x80", b"def f(:\n"):
            result = base.runner.run_binary(self.wasm, stdin=data)
            self.assertNotEqual(result.code, 0)
            self.assertEqual(result.stdout, b"")
            self.assertTrue(result.stderr)

    def test_bootstrap_failure_preserves_artifacts(self):
        directory = self.directory / "preserved"
        directory.mkdir()
        files = {"bodies.wasm": b"verified checker", "checker.stage2.wasm": b"verified stage 2",
                 "checker.stage3.wasm": b"verified stage 3", "checker-manifest.json": b"verified manifest"}
        for name, data in files.items():
            (directory / name).write_bytes(data)
        # A failure after the seed build must not publish any new stage.
        with patch("bootstrap.bootstrap_checker.build") as build_seed, \
             patch("bootstrap.bootstrap_checker.rebuild", side_effect=RuntimeError("proof failed")):
            def fake_build(work):
                (work / "bodies.bundle.py").write_text(self.source)
            build_seed.side_effect = fake_build
            with self.assertRaisesRegex(RuntimeError, "proof failed"):
                bootstrap(directory)
        self.assertEqual({p.name: p.read_bytes() for p in directory.iterdir()}, files)


if __name__ == "__main__":
    unittest.main(verbosity=2)
