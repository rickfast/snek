"""Benchmark-harness regressions; no Wasm toolchain required.

Run with the same Python used for the benchmarks: python tests/check_benchmarks.py.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bench"))
import bench
import compare_strings


class Benchmarks(unittest.TestCase):
    def test_flattening_preserves_output(self):
        for name in bench.BENCHES:
            with self.subTest(name=name):
                original = subprocess.run(
                    [sys.executable, "-c", f"import runpy; runpy.run_path({str(bench.PROGRAMS / (name + '.py'))!r}, run_name='__main__')"],
                    cwd=ROOT, capture_output=True, text=True, check=True)
                flat = subprocess.run([sys.executable, "-c", bench.flat_source(name)],
                                      capture_output=True, text=True, check=True)
                body, elapsed = bench.split_time(flat.stdout)
                self.assertEqual(original.stdout, body)
                self.assertIsNotNone(elapsed)

    def test_sample_validation(self):
        sample = {"code": 0, "out": "expected\n", "time": 1.0}
        self.assertTrue(bench.valid_sample(sample, "expected\n"))
        for change in ({"code": 1}, {"out": "wrong\n"}, {"time": None},
                       {"time": float("nan")}, {"time": float("inf")}, {"time": -1}):
            self.assertFalse(bench.valid_sample({**sample, **change}, "expected\n"))
        for bad in ("nan", "inf", "-1"):
            self.assertIsNone(bench.split_time(f"@@TIME {bad}\n")[1])

    def test_selection_rejects_typos(self):
        self.assertEqual(bench.selected("fib,strings", bench.BENCHES), ["fib", "strings"])
        with self.assertRaises(ValueError):
            bench.selected("typo", bench.BENCHES)

    def test_reference_failure_is_fatal(self):
        with patch.object(bench, "run_native", return_value={"code": 1, "out": "", "err": "broken", "time": None}):
            with self.assertRaisesRegex(RuntimeError, "reference failed"):
                bench.reference("fib")

    def test_rebuilds_existing_binary_once_per_invocation(self):
        with tempfile.TemporaryDirectory() as temp:
            src, out = Path(temp, "input.py"), Path(temp, "out.wasm")
            src.write_text("pass")
            out.write_bytes(b"stale")
            with patch.object(bench, "_builds", set()), patch.object(bench.subprocess, "run") as run:
                run.return_value.returncode = 0
                bench.snek_build(src, out)
                bench.snek_build(src, out)
                self.assertEqual(run.call_count, 1)
            with patch.object(bench, "_builds", set()), patch.object(bench.subprocess, "run") as run:
                run.return_value.returncode = 0
                bench.snek_build(src, out)
                run.assert_called_once()

    def test_fresh_run_discards_stale_results_and_returns_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp, "results.json")
            out.write_text(json.dumps({"old-runtime": {"fib": {"times": [999]}}}))
            ns = argparse.Namespace(cmd="run", only="fib", runtimes="cpython-native", reps=2, out=str(out))
            fake = lambda name: {"code": 0, "out": "wrong", "err": "", "time": .1}
            with patch.object(bench, "environment", return_value={}), \
                 patch.object(bench, "reference", return_value="correct"), \
                 patch.dict(bench.IN_PROCESS, {"cpython-native": fake}), contextlib.redirect_stdout(io.StringIO()):
                self.assertTrue(bench.cmd_run(ns))
            data = json.loads(out.read_text())
            self.assertNotIn("old-runtime", data)
            self.assertFalse(data["cpython-native"]["fib"]["ok"])
            self.assertEqual(data["cpython-native"]["fib"]["times"], [])
            self.assertTrue(data["_meta"]["complete"])

    def test_node_checks_every_sample(self):
        # First output is wrong, second would be right. The runner must stop at the first.
        if bench.node_major() < 24:
            self.skipTest("Node 24+ unavailable")
        with tempfile.TemporaryDirectory() as temp:
            rt = Path(temp)
            (rt / "pyodide").mkdir()
            (rt / "pyodide" / "pyodide.mjs").write_text('''
export async function loadPyodide() {
  let emit, count = 0;
  return {
    setStdout({ batched }) { emit = batched; },
    runPython() { emit(count++ === 0 ? 'wrong' : 'correct'); emit('@@TIME 0.1'); }
  };
}
''')
            with patch.object(bench, "RT", rt), patch.object(bench, "WORK", rt):
                data = bench.run_node("pyodide", [{"name": "test", "source": "", "expected": "correct\n"}], 2)
            self.assertFalse(data["results"]["test"]["ok"])
            self.assertEqual(data["results"]["test"]["times"], [])

    def test_snapshot_rejects_mixed_provenance_and_incomplete_runs(self):
        with tempfile.TemporaryDirectory() as temp:
            for meta in ({"environment": {"source": "old"}, "complete": True},
                         {"environment": {"source": "new"}, "complete": False}):
                Path(temp, "results.json").write_text(json.dumps({"_meta": meta}))
                with patch.object(bench, "WORK", Path(temp)), \
                     patch.object(bench, "environment", return_value={"source": "new"}):
                    with self.assertRaises(RuntimeError):
                        bench.cmd_snapshot(argparse.Namespace())

    def test_table_omits_failed_measurements(self):
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp, "results.json")
            out.write_text(json.dumps({"snek-v8": {"fib": {"ok": False, "times": [123.456]}}}))
            with contextlib.redirect_stdout(io.StringIO()) as stdout:
                bench.cmd_table(argparse.Namespace(file=str(out)))
            self.assertNotIn("123,456", stdout.getvalue())

    def test_string_comparison_checks_each_repetition(self):
        results = iter([{"code": 0, "out": "correct", "wall": .1},
                        {"code": 0, "out": "wrong", "wall": .1}])
        with self.assertRaisesRegex(RuntimeError, "output mismatch"):
            compare_strings.measure(lambda: next(results), "correct", 2)
        with self.assertRaises(RuntimeError):
            compare_strings.measure(lambda: {"code": 0, "out": "correct", "wall": float("nan")}, "correct", 1)

    def test_string_comparison_pins_collector_and_disables_cache(self):
        with patch.object(compare_strings.subprocess, "run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = "correct"
            run.return_value.stderr = ""
            result = compare_strings.cli_run(Path("strings.wasm"), "drc")
            command = run.call_args.args[0]
            self.assertIn("collector=drc", command)
            self.assertIn("cache=n", command)
            self.assertEqual(result["out"], "correct")
            self.assertEqual(result["code"], 0)


if __name__ == "__main__":
    unittest.main()
