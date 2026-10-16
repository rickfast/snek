"""Standalone CLI checks, including execution with no external tools on PATH."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest

ROOT = Path(__file__).resolve().parents[1]
BINARY = ROOT / "native/target/release" / ("snek.exe" if os.name == "nt" else "snek")


class NativeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="snek-native-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.binary = self.root / BINARY.name
        shutil.copy2(BINARY, self.binary)
        self.source = self.root / "source with spaces.py"
        self.source.write_text('print("hello")\n')

    def cli(self, *args, stdin=b"", **env):
        return subprocess.run([str(self.binary), *map(str, args)], cwd=self.root,
                              env={**os.environ, "PATH": "", **env}, input=stdin,
                              capture_output=True, timeout=120)

    def test_version_and_help(self):
        version = tomllib.loads((ROOT / "native/Cargo.toml").read_text())["package"]["version"]
        self.assertEqual(self.cli("--version").stdout, f"snek {version}\n".encode())
        self.assertEqual(self.cli("--help").returncode, 0)

    def test_run_without_external_dependencies(self):
        result = self.cli("run", self.source)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b"hello\n", b""))

    def test_opt_in_phase_timings(self):
        result = self.cli("--timings", "run", self.source)
        self.assertEqual((result.returncode, result.stdout), (0, b"hello\n"))
        self.assertTrue(result.stderr.startswith(b"SNEK_TIMINGS "))
        phases = json.loads(result.stderr.removeprefix(b"SNEK_TIMINGS "))
        self.assertGreater(phases["compiler_load_ms"], 0)
        self.assertGreater(phases["compiler_execute_ms"], 0)
        self.assertGreater(phases["guest_execute_ms"], 0)
        self.assertTrue(all(value >= 0 for value in phases.values()))
        self.assertGreaterEqual(phases["total_ms"], sum(v for k, v in phases.items() if k != "total_ms"))

    def test_build_check_and_wat(self):
        self.assertEqual(self.cli("check", self.source).returncode, 0)
        self.assertTrue(self.cli("wat", self.source).stdout.startswith(b"(module"))
        output = self.root / "output.wasm"
        result = self.cli("build", self.source, "-o", output)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(output.read_bytes().startswith(b"\0asm\x01\0\0\0"))

    def test_compile_failure_preserves_output(self):
        self.source.write_text("print(missing)\n")
        output = self.root / "output.wasm"
        output.write_bytes(b"previous build")
        result = self.cli("build", self.source, "-o", output)
        self.assertEqual(result.returncode, 1)
        self.assertIn(b"line 1", result.stderr)
        self.assertIn(b"missing", result.stderr)
        self.assertEqual(result.stdout, b"")
        self.assertEqual(output.read_bytes(), b"previous build")

    def test_source_cannot_be_overwritten(self):
        original = self.source.read_bytes()
        self.assertEqual(self.cli("build", self.source, "-o", self.source).returncode, 2)
        self.assertEqual(self.source.read_bytes(), original)

    def test_missing_input_and_bad_options(self):
        self.assertEqual(self.cli("run", self.root / "missing.py").returncode, 2)
        self.assertEqual(self.cli("build", self.source, "--component").returncode, 2)

    def test_guest_arguments_and_environment(self):
        self.source.write_text('from snek import sys, os\nprint(sys.argv[1:])\nprint(os.getenv("SNEK_TEST_SECRET", "hidden"))\n')
        result = self.cli("run", self.source, "--", "世界", "--flag", SNEK_TEST_SECRET="ambient")
        self.assertEqual(result.stdout, "['世界', '--flag']\nhidden\n".encode())
        result = self.cli("run", self.source, "--env", "SNEK_TEST_SECRET=granted")
        self.assertEqual(result.stdout, b"[]\ngranted\n")
        result = self.cli("run", self.source, "--env", "SNEK_TEST_SECRET", SNEK_TEST_SECRET="inherited")
        self.assertEqual(result.stdout, b"[]\ninherited\n")

    def test_guest_exit_code(self):
        self.source.write_text('raise ValueError("guest failure")\n')
        result = self.cli("run", self.source)
        self.assertEqual(result.returncode, 1)
        self.assertIn(b"ValueError: guest failure", result.stderr)

    def test_binary_stdio(self):
        payload = bytes(range(256)) * 1024
        result = self.cli("run", ROOT / "examples/stdlib_pipe.py", stdin=payload)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, payload)

    def test_native_compiler_reproduces_fixed_point(self):
        output = self.root / "compiler.wasm"
        result = self.cli("build", ROOT / "bootstrap/build/compiler.bundle.py", "-o", output)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(output.read_bytes(), (ROOT / "bootstrap/build/compiler.stage3.wasm").read_bytes())


def corpus():
    """Execute the same positive and negative corpus as the reference CLI."""
    failed = 0
    variants = json.loads((ROOT / "tests/selfhost_diagnostics.json").read_text())
    for source in sorted((ROOT / "examples").glob("*.py")):
        expected = subprocess.run([sys.executable, str(source)], capture_output=True,
                                  env={**os.environ, "PYTHONPATH": str(ROOT), "PYTHONIOENCODING": "utf-8"}, timeout=60)
        got = subprocess.run([str(BINARY), "run", str(source)], capture_output=True, timeout=60)
        # CPython's text stdout translates LF to CRLF on Windows; WASI does not.
        stdout = expected.stdout.replace(b"\r\n", b"\n") if os.name == "nt" else expected.stdout
        good = got.stdout == stdout and (got.returncode == 0) == (expected.returncode == 0)
        if expected.returncode:
            good = good and got.stderr.strip().splitlines()[-1:] == expected.stderr.strip().splitlines()[-1:]
        if not good:
            print("FAIL", source.name, got.returncode, got.stdout, got.stderr)
            failed += 1
    for source in sorted((ROOT / "tests/errors").glob("*.py")):
        want = variants.get(source.name, source.read_text().splitlines()[0].removeprefix("# error: "))
        got = subprocess.run([str(BINARY), "check", str(source)], capture_output=True, timeout=60)
        if got.returncode != 1 or want.encode() not in got.stderr or got.stdout:
            print("FAIL", source.name, got.returncode, got.stdout, got.stderr)
            failed += 1
    print(f"Native example/diagnostic corpus: {failed} failures", flush=True)
    return failed == 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=BINARY)
    parser.add_argument("--corpus", action="store_true")
    args = parser.parse_args()
    BINARY = args.binary.resolve()
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(NativeTests))
    ok = result.wasSuccessful()
    if args.corpus:
        ok = corpus() and ok
    raise SystemExit(not ok)
