"""Build Snek suspension lowering and frame emission with the owned checker."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bootstrap.body_bridge import compile_source
from bootstrap.linker import HEADER, PARSER_PRIMITIVES, module_source
from snekc.wasm import encode


def bundle_source() -> str:
    # Reuse only the parser's explicitly selected UTF-8 and tree primitives.
    source = HEADER + module_source("parser", select=PARSER_PRIMITIVES)
    source += "\n" + module_source("machine_lower") + "\n" + module_source("machine_emit")
    return source + '''

def machine_command() -> None:
    request = MachineJSON(utf8(read_bytes())).read()
    if request.kind == "MachineRequest":
        print(MachineGraph(request).tree().json())
    else:
        print(Node("MachineWAT").put("version", scalar(1)).put("wat", string(machine_emit_wat(request))).json())

machine_command()
'''


def main() -> None:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("-o", "--out", type=Path, default=ROOT / "bootstrap/build/machines.wasm")
    cli.add_argument("--checker-wasm", type=Path, default=ROOT / "bootstrap/build/bodies.wasm")
    args = cli.parse_args()
    if args.out.resolve() == args.checker_wasm.resolve() or args.out.suffix != ".wasm":
        cli.error("output must be a .wasm file distinct from the checker")
    binary = encode(compile_source(bundle_source(), args.checker_wasm))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(binary)
    print(f"built Snek suspension backend: {args.out} ({len(binary)} bytes)")


if __name__ == "__main__":
    main()
