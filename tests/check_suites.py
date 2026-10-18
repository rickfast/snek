"""Coverage inventory, isolation and fail-closed runner policy regressions."""
import ast
from contextlib import redirect_stderr, redirect_stdout
import importlib
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import run_suites as suites


class SuiteTests(unittest.TestCase):
    def test_inventory_covers_every_script(self):
        suites.validate_inventory()
        self.assertEqual(set(suites.selection(["all"])), set(suites.CHECKS))

    def test_inventory_rejects_new_missing_or_unassigned_scripts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "check_new.py").touch()
            with self.assertRaisesRegex(ValueError, "unregistered scripts: check_new.py"):
                suites.validate_inventory(root, {}, {})
            checks = {"new": suites.Check("check_new.py")}
            with self.assertRaisesRegex(ValueError, "checks in no suite"):
                suites.validate_inventory(root, checks, {})
            (root / "check_new.py").unlink()
            with self.assertRaisesRegex(ValueError, "missing scripts"):
                suites.validate_inventory(root, checks, {"fast": ("new",)})

    def test_selection_is_deterministic_and_deduplicates(self):
        self.assertEqual(suites.selection(["fast", "tooling"]), suites.selection(["fast"]))
        self.assertEqual(suites.selection(["compiler", "selfhost"]).count("selfhost_compiler"), 1)

    def test_pass_proofs_and_custom_hosts_have_explicit_prerequisites(self):
        for name in ("emitter_support", "machine_lowering", "language_components"):
            self.assertIn("checker", suites.CHECKS[name].requirements)
        for name in ("selfhost_bodies", "language_bodies", "benchmarks"):
            self.assertIn("wasmtime-py", suites.CHECKS[name].requirements)
        for name in ("bodies", "aggregate_bodies", "runtime_bodies", "ordered_bodies", "selfhost_bodies", "language_bodies"):
            self.assertIn("wasm-tools", suites.CHECKS[name].requirements)

    def test_compiler_preflight_requires_source_bundle(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build = root / "bootstrap/build"
            build.mkdir(parents=True)
            for name in ("compiler.wasm", "compiler.stage1.wasm", "compiler.stage2.wasm", "compiler.stage3.wasm", "compiler-manifest.json"):
                (build / name).touch()
            with patch.object(suites, "ROOT", root):
                with self.assertRaisesRegex(RuntimeError, "missing compiler.bundle.py"):
                    suites.prerequisites(("compiler",))
                (build / "compiler.bundle.py").touch()
                suites.prerequisites(("compiler",))

    def test_bundler_preflight_requires_installed_packages(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(suites, "ROOT", root):
                with self.assertRaisesRegex(RuntimeError, "npm ci --prefix examples/vite"):
                    suites.prerequisites(("bundler",))
                for name in ("vite", "typescript"):
                    package = root / "examples/vite/node_modules" / name / "package.json"
                    package.parent.mkdir(parents=True)
                    package.write_text("{}")
                suites.prerequisites(("bundler",))

    def test_unknown_suite_is_an_error(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            suites.main(["typo"])
        self.assertEqual(error.exception.code, 2)

    def test_failed_check_does_not_hide_later_checks(self):
        with patch.object(suites, "prerequisites"), \
             patch.object(suites.subprocess, "run") as run, redirect_stdout(io.StringIO()):
            count = len(suites.SUITES["reference"])
            run.side_effect = [subprocess.CompletedProcess([], code) for code in [1] + [0] * (count - 1)]
            self.assertTrue(suites.main(["reference"]))
            self.assertEqual(run.call_count, count)

    def script(self, source, **kwargs):
        with tempfile.TemporaryDirectory() as directory, redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            path = Path(directory) / "fixture.py"
            path.write_text(source)
            command = "import sys; sys.path.insert(0, " + repr(str(suites.TESTS)) + "); import run_suites; raise SystemExit(run_suites.worker(" + repr(str(path)) + ", **" + repr(kwargs) + "))"
            return subprocess.run([sys.executable, "-c", command], capture_output=True).returncode

    def test_skips_fail_unless_explicitly_allowed(self):
        source = '''import unittest
class Tests(unittest.TestCase):
    @unittest.skip("optional tool")
    def test_skipped(self): pass
unittest.main()
'''
        self.assertEqual(self.script(source), 1)
        self.assertEqual(self.script(source, allow_skips=True), 0)
        self.assertEqual(self.script(source, allowed_reasons=("optional tool",)), 0)
        self.assertEqual(self.script(source, allowed_reasons=("optional",)), 1)

    def test_empty_tests_and_ignored_failures_fail(self):
        self.assertEqual(self.script("import unittest\nunittest.main()\n"), 1)
        self.assertEqual(self.script('''import unittest
class Tests(unittest.TestCase):
    def test_failure(self): self.fail("expected")
unittest.TextTestRunner().run(unittest.defaultTestLoader.loadTestsFromTestCase(Tests))
'''), 1)

    def test_script_exit_codes_and_worker_state_are_preserved(self):
        runner = unittest.TextTestRunner.run
        self.assertEqual(self.script("raise SystemExit(7)\n"), 7)
        self.assertEqual(self.script("pass\n"), 0)
        self.assertIs(unittest.TextTestRunner.run, runner)

    def test_preflight_failure_runs_nothing(self):
        with patch.object(suites, "prerequisites", side_effect=RuntimeError("missing dependency")), \
             patch.object(suites.subprocess, "run") as run, \
             redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(suites.main(["fast"]), 2)
            run.assert_not_called()

    def test_worker_processes_have_explicit_compiler_and_clean_overrides(self):
        with patch.object(suites, "prerequisites"), \
             patch.object(suites.subprocess, "run") as run, \
             patch.dict(os.environ, {"SNEK_COMPILER": "snek", "SNEK_FRONTEND": "typo", "SNEK_COMPILER_WASM": "stale.wasm"}), \
             redirect_stdout(io.StringIO()):
            run.return_value.returncode = 0
            self.assertFalse(suites.main(["reference"]))
            for call in run.call_args_list:
                self.assertEqual(call.kwargs["env"]["SNEK_COMPILER"], "python")
                self.assertNotIn("SNEK_FRONTEND", call.kwargs["env"])
                self.assertNotIn("SNEK_COMPILER_WASM", call.kwargs["env"])

    def test_body_variants_keep_all_historical_cases_without_test_inheritance(self):
        expected = (14, 22, 29, 38, 46, 59)
        for suffix, classname, count in zip(
            ("bodies", "aggregate_bodies", "runtime_bodies", "ordered_bodies", "selfhost_bodies", "language_bodies"),
            ("BodyTests", "AggregateTests", "RuntimeTests", "OrderedTests", "SelfCheckTests", "LanguageTests"), expected,
        ):
            module = importlib.import_module("check_" + suffix)
            cls = getattr(module, classname)
            with self.subTest(variant=classname):
                self.assertEqual(len(unittest.defaultTestLoader.getTestCaseNames(cls)), count)
                self.assertEqual([base for base in cls.__mro__[1:] if issubclass(base, unittest.TestCase)], [unittest.TestCase])
                declared = {node.name for group in module.CASES
                            for node in ast.parse(Path(importlib.import_module(group.__module__).__file__).read_text()).body
                            if isinstance(node, ast.ClassDef) and node.name == group.__name__
                            for node in node.body if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")}
                self.assertEqual(set(unittest.defaultTestLoader.getTestCaseNames(cls)), declared)


if __name__ == "__main__":
    unittest.main(verbosity=2)
