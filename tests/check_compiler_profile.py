"""Regression checks for the diagnostic-only compiler phase profiler."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bench"))
import profile_compiler as pc


class ProfileTests(unittest.TestCase):
    def test_probe_is_separate_and_tracks_production_pipeline(self):
        source = pc.compiler_source()
        probe = pc.instrument(source)
        self.assertNotIn("SNEK_PROFILE", source)
        self.assertIn("from snek import time, f64", probe)
        self.assertNotIn("def emitter_command()", probe)
        self.assertTrue(probe.endswith("profile_command()\n"))
        with self.assertRaises(ValueError):
            pc.instrument(source.replace("    checked.run()\n", "    checked.changed()\n"))

    def test_valid_profile_requires_all_phases(self):
        line = b"SNEK_PROFILE " + b" ".join(f"{name}=1.5".encode() for name, _ in pc.STEPS) + b"\n"
        self.assertEqual(pc.parse_profile(line), {name: 1.5 for name, _ in pc.STEPS})
        for invalid in (line + line, line + b"unexpected diagnostic\n", b"SNEK_PROFILE read=1\n",
                        line.replace(b"1.5", b"nan"), line.replace(b"1.5", b"-1")):
            with self.assertRaises(ValueError):
                pc.parse_profile(invalid)


if __name__ == "__main__":
    unittest.main()
