"""Validation and statistics regressions for the standalone benchmark harness."""
import os
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bench"))
import native_bench as nb


class NativeBenchTests(unittest.TestCase):
    def test_statistics_keep_raw_samples(self):
        self.assertEqual(nb.stats([1, 2, 6]), {"median": 2, "mad": 1, "min": 1, "max": 6, "samples": [1, 2, 6]})
        for values in ([], [float("nan")], [float("inf")], [-1]):
            with self.assertRaises(ValueError):
                nb.stats(values)

    def test_output_and_exit_are_checked(self):
        valid = {"code": 0, "stdout": b"ok", "stderr": b""}
        nb.validate(valid, b"ok")
        for change in ({"code": 1}, {"stdout": b"wrong"}):
            with self.assertRaises(RuntimeError):
                nb.validate({**valid, **change}, b"ok")

    def test_rotates_routes_and_discards_warmup(self):
        calls = []
        def trial(name):
            calls.append(name)
            return {"wall_ms": len(calls), "peak_rss_mib": 1, "stderr": b""}
        rows = nb.interleaved({name: lambda name=name: trial(name) for name in ("a", "b")}, 2)
        self.assertEqual(calls, ["a", "b", "a", "b", "b", "a"])
        self.assertEqual(rows["a"]["wall_ms"]["samples"], [3, 6])

    def test_bad_phase_reports_are_rejected(self):
        base = {"wall_ms": 1, "peak_rss_mib": 1}
        for report in (b'SNEK_TIMINGS {"load": NaN}\n', b'SNEK_TIMINGS {}\nSNEK_TIMINGS {}\n'):
            with self.assertRaises((RuntimeError, ValueError)):
                nb.sample({**base, "stderr": report})

    def test_generated_function_names_do_not_collide_with_scalar_types(self):
        source = nb.generated_source(1000)
        self.assertNotIn("def f32(", source)
        self.assertNotIn("def f64(", source)
        self.assertEqual(source.count("def bench_fn_"), 1000)

    def test_compiler_comparisons_require_identical_output(self):
        for peer in ("baseline", "python-selfhost"):
            nb.verify_compiler_hashes({"native": "same", peer: "same"})
            with self.assertRaises(RuntimeError):
                nb.verify_compiler_hashes({"native": "same", peer: "different"})

    @unittest.skipUnless(hasattr(os, "wait4"), "requires POSIX wait4")
    def test_process_memory_is_per_child_and_output_is_exact(self):
        result = nb.process([sys.executable, "-c", "import sys; print('hello'); sys.exit(7)"])
        self.assertEqual((result["code"], result["stdout"]), (7, b"hello\n"))
        self.assertGreater(result["wall_ms"], 0)
        self.assertGreater(result["peak_rss_mib"], 0)


if __name__ == "__main__":
    unittest.main()
