"""Run stream/host component suites with every host compiler pass disabled.

The inherited alias tests' legacy parser selections are both routed to the
independent compiler. Compiler selection/conflict behavior is tested separately
in check_selfhost_compiler.py; this suite tests the actual library/host ABI.
"""
from contextlib import ExitStack
import os
import unittest
from unittest.mock import patch

from check_selfhost_compiler import no_host_compiler
from check_stdlib import StdlibTests
from check_host_library import HostLibraryTests
from snekc import driver


class IndependentCompiler:
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        guards = ExitStack()
        cls.addClassCleanup(guards.close)
        guards.enter_context(patch.dict(os.environ, {"SNEK_COMPILER": "snek", "SNEK_RUNTIME": "wasmtime"}))
        compile_original = driver.compile_to_wat

        def compile_owned(source, filename="<input>", *, component=False, frontend=None, compiler=None):
            return compile_original(source, filename, component=component, compiler="snek")

        guards.enter_context(patch("snekc.driver.compile_to_wat", compile_owned))
        guards.enter_context(no_host_compiler())


class SelfhostStreamTests(IndependentCompiler, StdlibTests):
    pass


class SelfhostHostTests(IndependentCompiler, HostLibraryTests):
    pass


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-no-skips", action="store_true",
                        help="CI gate: require optimized tests and fail on any skipped test")
    args = parser.parse_args()
    if args.require_no_skips:
        # The shared suites allow raw-only development without Binaryen.
        # CI must exercise both modes, not silently reduce its coverage.
        driver.find_wasm_opt()
    suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromTestCase(cls)
                               for cls in (SelfhostStreamTests, SelfhostHostTests))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful() or (args.require_no_skips and bool(result.skipped)))
