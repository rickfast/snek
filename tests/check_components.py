"""Differential WASI 0.3 component tests, including validation and WIT targets.

Requires Binaryen, wasm-tools 1.250+, Wasmtime CLI 49+, and CPython 3.13.
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

from run_tests import ROOT, check_example

from snekc import driver


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-k", default="")
    ns = ap.parse_args()
    os.environ["SNEK_RUNTIME"] = "wasmtime"
    build = driver.build

    def component_build(*args, **kwargs):
        result = build(*args, component=True, **kwargs)
        # Verify the delivered standalone WIT, not only the source WIT package.
        driver.wasm_tools("component", "targets", str(result.wit_path), "-w", "command", str(result.wasm_path))
        if "wasi_snapshot_preview1" in result.wat:
            raise AssertionError("preview1 import leaked into component")
        return result

    driver.build = component_build
    failed = passed = 0
    with tempfile.TemporaryDirectory(prefix="snek-components-") as d:
        for src in sorted((ROOT / "examples").glob("*.py")):
            if ns.k not in src.stem:
                continue
            problems = check_example(src, Path(d))
            if problems:
                failed += 1
                print("FAIL", src.stem, "\n" + "\n".join(problems))
            else:
                passed += 1
                print("ok  ", src.stem)
    print(f"\n{passed} passed, {failed} failed")
    return bool(failed)


if __name__ == "__main__":
    sys.exit(main())
