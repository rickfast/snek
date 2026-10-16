"""Prove the Snek binary backend bootstrap; this is not a self-hosted frontend.

Python produces WAT once. A seed encoder, compiled by Python, encodes that WAT
into stage 2; stage 2 produces stage 3. All three binaries must match exactly.
No optimizer is used. Only after verification are artifacts published.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from snekc import driver, runner
from snekc.wasm import encode

SOURCE = ROOT / "bootstrap/encode.py"


def assemble(encoder: Path, wat: str) -> bytes:
    result = runner.run_binary(encoder, stdin=wat.encode("utf-8"))
    if result.code:
        raise RuntimeError(f"bootstrap encoder exited {result.code}:\n{result.stderr.decode('utf-8', errors='replace')}")
    if result.stderr or not result.stdout.startswith(b"\0asm\x01\0\0\0"):
        raise RuntimeError("bootstrap encoder did not produce a clean core binary")
    return result.stdout


def bootstrap(directory: Path, *, check_examples: bool = False) -> dict:
    # Preserve already verified artifacts if any stage or comparison fails.
    with tempfile.TemporaryDirectory(prefix="snek-bootstrap-") as temporary:
        work = Path(temporary)
        source = SOURCE.read_text()
        wat = driver.compile_to_wat(source, str(SOURCE))
        seed = encode(wat, debug_names=False)
        stages = [work / f"encoder.stage{i}.wasm" for i in (1, 2, 3)]
        stages[0].write_bytes(seed)
        for previous, target in zip(stages, stages[1:]):
            result = assemble(previous, wat)
            if result != seed:
                raise RuntimeError(f"{target.name}: bootstrap binary differs from the seed")
            target.write_bytes(result)
            print(f"ok {previous.name} -> {target.name}: {len(result):,} identical bytes", flush=True)
        # Execute stage 3 as well; do not merely compare an unexecutable file.
        hello = driver.compile_to_wat('print("bootstrap works")\n')
        program = work / "hello.wasm"
        program.write_bytes(assemble(stages[-1], hello))
        if program.read_bytes() != encode(hello, debug_names=False):
            raise RuntimeError("stage 3 failed the program binary comparison")
        result = runner.run_binary(program)
        if result.code or result.stdout != b"bootstrap works\n" or result.stderr:
            raise RuntimeError("stage 3's emitted program did not run correctly")
        checked = 0
        if check_examples:
            for src in sorted((ROOT / "examples").glob("*.py")):
                for component in (False, True):
                    text = driver.compile_to_wat(src.read_text(), str(src), component=component)
                    if assemble(stages[-1], text) != encode(text, debug_names=False):
                        raise RuntimeError(f"binary mismatch: {src.name}, component core={component}")
                    checked += 1
                print(f"ok {src.name}: core + component core", flush=True)
        manifest = {
            "scope": "WAT-to-core-Wasm backend only; the Python frontend is still required",
            "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
            "wat_sha256": hashlib.sha256(wat.encode()).hexdigest(),
            "binary_sha256": hashlib.sha256(seed).hexdigest(),
            "bytes": len(seed),
            "stages": [p.name for p in stages],
            "runtime": runner.available_runtime(),
            "example_binaries_compared": checked,
            "optimizer": None,
        }
        directory.mkdir(parents=True, exist_ok=True)
        for stage in stages:
            shutil.copyfile(stage, directory / stage.name)
        (directory / "encoder.wat").write_text(wat)
        (directory / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "bootstrap/build")
    parser.add_argument("--check-examples", action="store_true", help="compare both core targets for every example")
    args = parser.parse_args()
    try:
        manifest = bootstrap(args.out, check_examples=args.check_examples)
    except (driver.CompileError, driver.ToolError, RuntimeError) as error:
        print(f"bootstrap: {error}", file=sys.stderr)
        return 1
    print(f"Backend bootstrap verified: {manifest['binary_sha256']}")
    print(f"Artifacts: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
