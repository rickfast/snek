"""snekc: compile a statically typed Python subset to WebAssembly (WasmGC + WASI).

  python -m snekc check prog.py           type-check only
  python -m snekc wat   prog.py           print the generated WAT
  python -m snekc build prog.py [-o out.wasm] [--no-opt] [--keep-wat]
  python -m snekc run   prog.py [args...]
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

from . import driver, runner
from .errors import CompileError
from .frontend import FRONTENDS


def source_arguments(command: argparse.ArgumentParser) -> None:
    command.add_argument("src", type=Path)
    command.add_argument("--module-root", type=Path, help="local import root (default: entry file's directory)")
    command.add_argument("--frontend", choices=FRONTENDS, help="source parser (default: SNEK_FRONTEND or cpython)")
    command.add_argument("--compiler", choices=("python", "snek"), help="compiler implementation (default: SNEK_COMPILER or python)")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="snekc", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("check", "wat"):
        source_arguments(sub.add_parser(name))
    b = sub.add_parser("build")
    source_arguments(b)
    b.add_argument("-o", "--out", type=Path)
    b.add_argument("--no-opt", action="store_true", help="use native encoding only (no Binaryen needed)")
    b.add_argument("--keep-wat", action="store_true", help="keep the .wat next to the .wasm")
    b.add_argument("--component", action="store_true", help="emit a WASI 0.3 command component and WIT")
    r = sub.add_parser("run")
    source_arguments(r)
    r.add_argument("--no-opt", action="store_true", help="use native encoding only (no Binaryen needed)")
    r.add_argument("--component", action="store_true", help="run a WASI 0.3 component with Wasmtime 49+")
    r.add_argument("--env", action="append", default=[], metavar="NAME[=VALUE]",
                   help="grant a guest environment variable (NAME alone inherits that variable)")
    ns, extra = ap.parse_known_args(argv)
    if extra and ns.cmd != "run":
        ap.error("unrecognized arguments: " + " ".join(extra))
    args = extra[1:] if extra[:1] == ["--"] else extra
    grants = {}
    for grant in getattr(ns, "env", []):
        name, separator, value = grant.partition("=")
        if not name:
            ap.error("--env requires a nonempty variable name")
        if separator:
            grants[name] = value
        elif name in os.environ:
            grants[name] = os.environ[name]

    source = None
    try:
        if ns.cmd in ("check", "wat"):
            source = ns.src.read_text()
        if ns.cmd == "check":
            driver.compile_to_wat(source, str(ns.src), frontend=ns.frontend, compiler=ns.compiler, module_root=ns.module_root)
            print(f"{ns.src}: ok")
            return 0
        if ns.cmd == "wat":
            sys.stdout.write(driver.compile_to_wat(source, str(ns.src), frontend=ns.frontend, compiler=ns.compiler, module_root=ns.module_root))
            return 0
        if ns.cmd == "build":
            res = driver.build(ns.src, ns.out, optimize=not ns.no_opt, keep_wat=ns.keep_wat,
                               component=ns.component, frontend=ns.frontend, compiler=ns.compiler, module_root=ns.module_root)
            print(f"wrote {res.wasm_path} ({res.wasm_path.stat().st_size} bytes)")
            if res.wit_path:
                print(f"wrote {res.wit_path}")
            return 0
        with tempfile.TemporaryDirectory() as d:
            wasm = Path(d, ns.src.stem + ".wasm")
            driver.build(ns.src, wasm, optimize=not ns.no_opt, component=ns.component, frontend=ns.frontend, compiler=ns.compiler, module_root=ns.module_root)
            res = runner.run_binary(wasm, args, stdin=None, env=grants)
        sys.stdout.buffer.write(res.stdout)
        sys.stderr.buffer.write(res.stderr)
        return res.code
    except CompileError as e:
        if source is None:
            try:
                source = ns.src.read_text()
            except (OSError, UnicodeError):
                pass  # A disappearing source must not hide the compile error.
        print(e.format(str(ns.src), source), file=sys.stderr)
        return 1
    except (driver.ToolError, RuntimeError, ValueError, OSError) as e:
        print(f"snekc: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
