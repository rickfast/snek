"""Fast regressions for build orchestration, target metadata, and benchmark identity."""
from contextlib import redirect_stdout, redirect_stderr
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


packages = load("snek_build_packages", "packaging/build_packages.py")
native = load("snek_build_native", "packaging/build_native.py")
common = load("snek_bench_common", "bench/common.py")


class ToolingTests(unittest.TestCase):
    def test_matrix_and_packages_share_target_records(self):
        rows = packages.ci_matrix()["include"]
        self.assertEqual(len(rows), len(packages.TARGETS))
        for row in rows:
            platform, arch, tag = packages.TARGETS[row["target"]]
            self.assertEqual(row["executable"], "snek.exe" if platform == "win32" else "snek")
            self.assertEqual(row.get("wheel_platform"), tag if platform == "linux" else None)
        with redirect_stdout(io.StringIO()) as out:
            packages.main(["--matrix"])
        self.assertEqual(json.loads(out.getvalue()), packages.ci_matrix())

    def test_target_manifest_fails_closed(self):
        valid = packages.TARGET_RECORDS[0]
        cases = [[], {}, [valid, valid], [{**valid, "unexpected": "x"}],
                 [{**valid, "os": ""}], [{**valid, "npm_platform": "unsupported"}],
                 [valid, {**valid, "target": "different"}]]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "targets.json"
            for records in cases:
                with self.subTest(records=records):
                    path.write_text(json.dumps(records))
                    with self.assertRaises(ValueError):
                        packages.load_targets(path)

    def test_package_cli_requires_build_inputs(self):
        for args in ([], ["--binary", "test"], ["--matrix", "--binary", "test"]):
            with self.subTest(args=args), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    packages.main(args)
                self.assertEqual(error.exception.code, 2)

    def test_native_build_always_verifies_fresh_bootstrap_before_cargo(self):
        with patch.object(native.subprocess, "run") as run:
            native.build(offline=True)
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(commands[0], [sys.executable, "bootstrap/bootstrap_compiler.py"])
        self.assertEqual(commands[1], [sys.executable, "tests/check_selfhost_compiler.py"])
        self.assertEqual([c[2] for c in commands[2:]], ["build", "test"])
        for call in run.call_args_list:
            self.assertEqual(call.kwargs["cwd"], ROOT)
            self.assertTrue(call.kwargs["check"])
        for command in commands[2:]:
            self.assertIn("--locked", command)
            self.assertIn("--offline", command)

    def test_failed_bootstrap_does_not_build_old_artifacts(self):
        failure = subprocess.CalledProcessError(1, ["bootstrap"])
        with patch.object(native.subprocess, "run", side_effect=failure) as run:
            with self.assertRaises(subprocess.CalledProcessError):
                native.build()
        self.assertEqual(run.call_count, 1)

    def test_native_main_reports_missing_tool(self):
        with patch.object(native, "build", side_effect=FileNotFoundError("cargo")), redirect_stderr(io.StringIO()) as err:
            self.assertEqual(native.main([]), 1)
        self.assertIn("cargo", err.getvalue())

    def test_source_identity_includes_runtime_resources_not_results(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            resource = root / "runtime.snek"
            resource.write_text("print(1)\n")
            result = root / "bench/results/result.json"
            result.parent.mkdir(parents=True)
            result.write_text("{}")
            names = b"runtime.snek\0bench/results/result.json\0"
            with patch.object(common.subprocess, "check_output", return_value=names):
                first = common.source_identity(root)
                result.write_text('{"measurement": 1}')
                self.assertEqual(common.source_identity(root), first)
                resource.write_text("print(2)\n")
                self.assertNotEqual(common.source_identity(root), first)

    def test_statistics_reject_non_measurements(self):
        self.assertEqual(common.stats([1, 2, 6])["mad"], 1)
        for values in ([], [-1], [float("nan")], [float("inf")]):
            with self.assertRaises(ValueError):
                common.stats(values)


if __name__ == "__main__":
    unittest.main()
