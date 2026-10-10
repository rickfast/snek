"""Test runner.

  examples/*.py      differential: compiled Wasm must print exactly what CPython
                     prints (with the snek shim) and exit with the same status.
                     Each example runs both unoptimized and through wasm-opt -O3.
  tests/errors/*.py  must fail to compile; the first line `# error: <text>`
                     must appear in the error message.

  python tests/run_tests.py [-k substring]
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from snekc import CompileError, driver, runner  # noqa: E402


def run_cpython(src: Path) -> runner.RunResult:
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    p = subprocess.run([sys.executable, str(src)], capture_output=True, text=True, env=env)
    return runner.RunResult(p.returncode, p.stdout, p.stderr)


def check_example(src: Path, tmp: Path) -> list[str]:
    failures = []
    expected = run_cpython(src)
    for optimize in (False, True):
        wasm = tmp / f"{src.stem}.{'opt' if optimize else 'raw'}.wasm"
        try:
            driver.build(src, wasm, optimize=optimize)
        except CompileError as e:
            return [e.format(str(src))]
        got = runner.run(wasm)
        mode = "-O3" if optimize else "raw"
        if got.stdout != expected.stdout:
            failures.append(f"[{mode}] stdout differs\n--- cpython\n{expected.stdout}--- wasm\n{got.stdout}"
                            f"{got.stderr}")
        if (got.code == 0) != (expected.code == 0):
            failures.append(f"[{mode}] exit status {got.code}, cpython {expected.code}\n{got.stderr}")
    return failures


def check_error(src: Path) -> list[str]:
    source = src.read_text()
    first = source.splitlines()[0]
    assert first.startswith("# error: "), f"{src} must start with '# error: ...'"
    want = first.removeprefix("# error: ").strip()
    try:
        driver.compile_to_wat(source, str(src))
    except CompileError as e:
        return [] if want in e.msg else [f"wrong error:\n  want: {want}\n  got:  {e.msg}"]
    return ["compiled without error"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-k", default="", help="only run tests whose name contains this")
    ns = ap.parse_args()
    examples = sorted((ROOT / "examples").glob("*.py"))
    errors = sorted((ROOT / "tests" / "errors").glob("*.py"))
    passed = failed = 0
    with tempfile.TemporaryDirectory() as d:
        for kind, files in (("example", examples), ("error", errors)):
            for src in files:
                if ns.k not in src.stem:
                    continue
                problems = check_example(src, Path(d)) if kind == "example" else check_error(src)
                if problems:
                    failed += 1
                    print(f"FAIL {kind} {src.stem}")
                    for p in problems:
                        print("  " + p.replace("\n", "\n  "))
                else:
                    passed += 1
                    print(f"ok   {kind} {src.stem}")
    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
