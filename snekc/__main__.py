"""snekc: compile a statically typed Python subset to WebAssembly (WasmGC + WASI).

  python -m snekc check prog.py           type-check only
  python -m snekc wat   prog.py           print the generated WAT
  python -m snekc build prog.py [-o out.wasm] [--no-opt] [--keep-wat]
  python -m snekc run   prog.py [args...]
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

from . import driver, runner
from .errors import CompileError


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="snekc", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("check", "wat"):
        sub.add_parser(name).add_argument("src", type=Path)
    b = sub.add_parser("build")
    b.add_argument("src", type=Path)
    b.add_argument("-o", "--out", type=Path)
    b.add_argument("--no-opt", action="store_true", help="skip wasm-opt -O3 (just assemble)")
    b.add_argument("--keep-wat", action="store_true", help="keep the .wat next to the .wasm")
    r = sub.add_parser("run")
    r.add_argument("src", type=Path)
    r.add_argument("--no-opt", action="store_true")
    r.add_argument("args", nargs=argparse.REMAINDER)
    ns = ap.parse_args(argv)

    source = ns.src.read_text()
    try:
        if ns.cmd == "check":
            driver.compile_to_wat(source, str(ns.src))
            print(f"{ns.src}: ok")
            return 0
        if ns.cmd == "wat":
            sys.stdout.write(driver.compile_to_wat(source, str(ns.src)))
            return 0
        if ns.cmd == "build":
            res = driver.build(ns.src, ns.out, optimize=not ns.no_opt, keep_wat=ns.keep_wat)
            print(f"wrote {res.wasm_path} ({res.wasm_path.stat().st_size} bytes)")
            return 0
        with tempfile.TemporaryDirectory() as d:
            wasm = Path(d, ns.src.stem + ".wasm")
            driver.build(ns.src, wasm, optimize=not ns.no_opt)
            res = runner.run(wasm, ns.args)
        sys.stdout.write(res.stdout)
        sys.stderr.write(res.stderr)
        return res.code
    except CompileError as e:
        print(e.format(str(ns.src), source), file=sys.stderr)
        return 1
    except (driver.ToolError, RuntimeError) as e:
        print(f"snekc: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
