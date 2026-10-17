"""Independent source-to-binary compiler and CLI integration regression gates."""
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bootstrap.build_emitter import compiler_source
from bootstrap.build_bodies import SMOKE_SOURCE
from snekc import driver, runner, selfhost
from snekc.errors import CompileError


def no_host_compiler():
    stack = ExitStack()
    for target in ("ast.parse", "snekc.check.Checker.run", "snekc.driver.check_module",
                   "snekc.driver.emit_module", "snekc.driver.encode", "snekc.emit_wat.emit_module",
                   "snekc.wasm.encode", "bootstrap.body_bridge.to_module", "bootstrap.body_bridge.check_source"):
        stack.enter_context(patch(target, side_effect=AssertionError("host compiler fallback: " + target)))
    return stack


class SelfhostCompilerTests(unittest.TestCase):
    def test_published_fixed_point_matches_current_source(self):
        directory = ROOT / "bootstrap/build"
        manifest = json.loads((directory / "compiler-manifest.json").read_text())
        self.assertEqual(hashlib.sha256(compiler_source().encode()).hexdigest(), manifest["source_sha256"])
        self.assertTrue(manifest["stage2_equals_stage3"])
        self.assertTrue(manifest["host_fallback_guards"])
        self.assertFalse(manifest["host_passes_after_seed"])
        for stage in manifest["stages"]:
            data = (directory / stage["file"]).read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), stage["sha256"])
        stage2 = (directory / "compiler.stage2.wasm").read_bytes()
        self.assertEqual(stage2, (directory / "compiler.stage3.wasm").read_bytes())
        self.assertEqual(stage2, (directory / "compiler.wasm").read_bytes())

    def test_binary_and_wat_routes_without_host_passes(self):
        with no_host_compiler():
            direct = selfhost.invoke(SMOKE_SOURCE.encode(), [])
            wat = driver.compile_to_wat(SMOKE_SOURCE, compiler="snek")
            self.assertEqual(direct, selfhost.encode(wat))
            with tempfile.TemporaryDirectory() as d:
                p = Path(d) / "program.wasm"
                p.write_bytes(direct)
                result = runner.run_binary(p)
                self.assertEqual((result.code, result.stdout, result.stderr), (0, b"55\n", b""))

    def test_build_uses_one_direct_compiler_invocation(self):
        with no_host_compiler(), tempfile.TemporaryDirectory() as d:
            source = Path(d, "program.py")
            source.write_text(SMOKE_SOURCE)
            with patch.object(selfhost, "invoke", wraps=selfhost.invoke) as invoke:
                result = driver.build(source, optimize=False, compiler="snek")
            from snekc.project import Project
            invoke.assert_called_once_with(Project(source, SMOKE_SOURCE).envelope().encode(), ["--project"])
            self.assertIsNone(result.wat)
            self.assertEqual(result.wasm_path.read_bytes(), selfhost.encode(selfhost.wat(SMOKE_SOURCE)))
            executed = runner.run_binary(result.wasm_path)
            self.assertEqual((executed.code, executed.stdout, executed.stderr), (0, b"55\n", b""))

    def test_suspension_and_closures_without_host_passes(self):
        source = (ROOT / "examples/closures_suspended.py").read_text()
        with no_host_compiler(), tempfile.TemporaryDirectory() as d:
            p = Path(d) / "program.wasm"
            p.write_bytes(selfhost.invoke(source.encode(), []))
            result = runner.run_binary(p)
            self.assertEqual(result.code, 0)
            self.assertEqual(result.stderr, b"")
            self.assertTrue(result.stdout)

    def test_diagnostics_and_no_partial_output(self):
        with no_host_compiler():
            with self.assertRaises(CompileError) as error:
                driver.compile_to_wat("print(missing)", compiler="snek")
            self.assertEqual(error.exception.lineno, 1)
            self.assertIn("missing", error.exception.msg)
            result = runner.run_binary(ROOT / "bootstrap/build/compiler.wasm", stdin=b"print(missing)")
            self.assertNotEqual(result.code, 0)
            self.assertEqual(result.stdout, b"")

    def test_missing_or_invalid_compiler_never_falls_back(self):
        with no_host_compiler(), patch.dict(os.environ, {"SNEK_COMPILER_WASM": "/nonexistent/snek-compiler.wasm"}):
            with self.assertRaisesRegex(RuntimeError, "not found"):
                driver.compile_to_wat("print(1)", compiler="snek")
        with no_host_compiler(), patch("snekc.runner.run_binary", return_value=runner.BinaryRunResult(0, b"bad", b"")):
            with self.assertRaisesRegex(RuntimeError, "core WAT"):
                driver.compile_to_wat("print(1)", compiler="snek")

    def test_selection_and_frontend_conflicts(self):
        with patch.dict(os.environ, {"SNEK_COMPILER": "snek"}):
            self.assertTrue(selfhost.selected())
            self.assertFalse(selfhost.selected("python"))
        with self.assertRaisesRegex(ValueError, "owns its frontend"):
            driver.compile_to_wat("print(1)", frontend="cpython", compiler="snek")
        with self.assertRaises(ValueError): selfhost.selected("invalid")

    def test_diagnostic_variants_are_specific_existing_fixtures(self):
        variants = json.loads((ROOT / "tests/selfhost_diagnostics.json").read_text())
        for name, message in variants.items():
            self.assertTrue((ROOT / "tests/errors" / name).is_file(), name)
            self.assertGreater(len(message), 15, name)

    def test_failed_build_preserves_destination(self):
        with no_host_compiler(), tempfile.TemporaryDirectory() as d:
            source, output = Path(d) / "bad.py", Path(d) / "old.wasm"
            source.write_text("print(missing)")
            output.write_bytes(b"previous artifact")
            with self.assertRaises(CompileError): driver.build(source, output, optimize=False, compiler="snek")
            self.assertEqual(output.read_bytes(), b"previous artifact")


if __name__ == "__main__":
    unittest.main()
