"""Run the real CLI/component host suite through the rebuilt Snek checker.

The Python emitter remains the explicit bridge. Python source parsing/body
checking are disabled, including while building component-host test guests.
When selected through the machine environment variables, the Snek suspension
passes also run with their Python reference implementations disabled.
Requires Wasmtime CLI and wasm-tools; set SNEK_RUNTIME=wasmtime.
"""
from contextlib import ExitStack, nullcontext
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from check_stdlib import StdlibTests, ROOT
from bootstrap.body_bridge import compile_source
from snekc.check import Checker
from snekc.errors import CompileError


class LanguageComponentTests(StdlibTests):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        checker = Path(ROOT / "bootstrap/build/bodies.wasm")
        if not checker.exists():
            raise RuntimeError("run bootstrap/bootstrap_checker.py first")

        def compile_owned(source, filename="<input>", *, component=False, frontend=None):
            try:
                with ExitStack() as guards, patch.object(Checker, "run", side_effect=AssertionError("Python checker fallback")), \
                     patch("ast.parse", side_effect=AssertionError("Python source parser fallback")), \
                     (patch("snekc.machines.Graph", side_effect=AssertionError("Python CFG fallback"))
                      if os.environ.get("SNEK_MACHINE_LOWERER") else nullcontext()):
                    if os.environ.get("SNEK_MACHINE_EMITTER"):
                        for method in ("emit_machine_python", "machine_type_section_python", "machine_helpers_python", "machine_fields"):
                            guards.enter_context(patch("snekc.machines.MachineEmitter." + method,
                                                       side_effect=AssertionError("Python machine emission fallback")))
                    return compile_source(source, checker, component=component)
            except ValueError as error:
                # Adapt the experimental CLI's diagnostic exception to the
                # production driver's public interface used by this suite.
                raise CompileError(str(error)) from error

        guard = patch("snekc.driver.compile_to_wat", compile_owned)
        guard.start()
        cls.addClassCleanup(guard.stop)


if __name__ == "__main__":
    # Do not rediscover the imported base class as a second, Python-checked run.
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(LanguageComponentTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
