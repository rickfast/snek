"""Development CLI argument forwarding, diagnostics and I/O failure handling."""
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from snekc import __main__ as cli, driver, runner
from snekc.errors import CompileError


class Capture(io.StringIO):
    def __init__(self):
        super().__init__()
        self.buffer = io.BytesIO()


class CliTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="snek-cli-")
        self.addCleanup(directory.cleanup)
        self.source = Path(directory.name, "program.py")
        self.source.write_text("print(missing)\n")
        self.output = Path(directory.name, "program.wasm")
        self.output.write_bytes(b"wasm")
        self.stdout, self.stderr = Capture(), Capture()
        self.enterContext(patch.object(sys, "stdout", self.stdout))
        self.enterContext(patch.object(sys, "stderr", self.stderr))

    def test_missing_files_are_reported_without_tracebacks(self):
        missing = str(self.source.with_name("missing.py"))
        for command in ("check", "wat", "build", "run"):
            with self.subTest(command=command):
                self.assertEqual(cli.main([command, missing]), 2)
        self.assertEqual(self.stderr.getvalue().count("snekc:"), 4)
        self.assertIn("missing.py", self.stderr.getvalue())
        self.assertNotIn("Traceback", self.stderr.getvalue())

    def test_build_and_run_do_not_read_source_before_building(self):
        with patch.object(Path, "read_text", side_effect=AssertionError("redundant source read")), \
             patch.object(driver, "build", return_value=driver.Result(None, self.output)) as build, \
             patch.object(runner, "run_binary", return_value=runner.BinaryRunResult(7, b"out", b"err")) as run:
            flags = [str(self.source), "--no-opt", "--frontend", "snek", "--compiler", "snek"]
            self.assertEqual(cli.main(["build", *flags]), 0)
            self.assertEqual(cli.main(["run", *flags, "--", "--guest", "hello"]), 7)
        for call in build.call_args_list:
            self.assertEqual(call.kwargs["frontend"], "snek")
            self.assertEqual(call.kwargs["compiler"], "snek")
            self.assertFalse(call.kwargs["optimize"])
        self.assertEqual(run.call_args.args[1], ["--guest", "hello"])
        self.assertEqual(self.stdout.buffer.getvalue(), b"out")
        self.assertEqual(self.stderr.buffer.getvalue(), b"err")

    def test_check_and_wat_share_compiler_arguments(self):
        with patch.object(driver, "compile_to_wat", return_value="(module)") as compile:
            for command in ("check", "wat"):
                self.assertEqual(cli.main([command, str(self.source), "--frontend", "snek", "--compiler", "python"]), 0)
        self.assertEqual(compile.call_count, 2)
        for call in compile.call_args_list:
            self.assertEqual(call.args, ("print(missing)\n", str(self.source)))
            self.assertEqual(call.kwargs, {"frontend": "snek", "compiler": "python"})

    def test_compile_error_reads_source_only_for_diagnostic(self):
        error = CompileError("unknown name 'missing'")
        error.lineno, error.col = 1, 6
        with patch.object(driver, "build", side_effect=error):
            self.assertEqual(cli.main(["build", str(self.source)]), 1)
        self.assertIn("print(missing)", self.stderr.getvalue())
        self.assertIn(":1:7: error:", self.stderr.getvalue())

    def test_unreadable_diagnostic_source_does_not_mask_compile_error(self):
        with patch.object(driver, "build", side_effect=CompileError("original diagnostic")), \
             patch.object(Path, "read_text", side_effect=PermissionError("denied")):
            self.assertEqual(cli.main(["build", str(self.source)]), 1)
        self.assertIn("original diagnostic", self.stderr.getvalue())
        self.assertNotIn("denied", self.stderr.getvalue())

    def test_output_io_error_is_reported(self):
        with patch.object(driver, "build", side_effect=PermissionError("output denied")):
            self.assertEqual(cli.main(["build", str(self.source)]), 2)
        self.assertIn("snekc: output denied", self.stderr.getvalue())

    def test_guest_arguments_and_environment_grants_are_preserved(self):
        with patch.dict(cli.os.environ, {"SNEK_TEST_INHERIT": "inherited"}), \
             patch.object(driver, "build"), \
             patch.object(runner, "run_binary", return_value=runner.BinaryRunResult(0, b"", b"")) as run:
            self.assertEqual(cli.main(["run", str(self.source), "--env", "SNEK_TEST_INHERIT", "--env", "EXPLICIT=value",
                                       "--", "--compiler", "guest", "two words"]), 0)
        self.assertEqual(run.call_args.args[1], ["--compiler", "guest", "two words"])
        self.assertEqual(run.call_args.kwargs, {"stdin": None, "env": {"SNEK_TEST_INHERIT": "inherited", "EXPLICIT": "value"}})


if __name__ == "__main__":
    unittest.main()
