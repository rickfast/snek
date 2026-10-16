"""Rebuild the Snek parser/checker from its source using a Python emitter bridge.

This proves a checker fixed point, not full-compiler self-hosting. The Python
compiler builds the seed. Subsequent stages run that checker on the unchanged
Snek source, then emit its resolved IR through body_bridge/emit_wat/wasm.
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

from bootstrap.body_bridge import check_source, to_module
from bootstrap.build_bodies import SMOKE_SOURCE, build
from snekc import driver, runner
from snekc.emit_wat import emit_module
from snekc.wasm import encode


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def rebuild(source: str, seed: Path, directory: Path) -> dict:
    """Verify stages in a caller-owned scratch directory; never publish here."""
    expected = check_source(source)
    stages = [seed, directory / "checker.stage2.wasm", directory / "checker.stage3.wasm"]
    previous = seed
    for stage in stages[1:]:
        actual = check_source(source, previous)
        if actual != expected:
            raise RuntimeError(f"{previous.name}: self-source typed IR differs from the Snek shim")
        stage.write_bytes(encode(emit_module(to_module(actual)), debug_names=False))
        previous = stage
    if stages[1].read_bytes() != stages[2].read_bytes():
        raise RuntimeError("checker stage 2 and stage 3 binaries differ")
    # Execute the final stage on itself too, rather than only comparing files.
    if check_source(source, stages[-1]) != expected:
        raise RuntimeError("stage 3 self-source typed IR differs from the Snek shim")

    checked = 0
    for fixture in sorted((ROOT / "examples").glob("*.py")):
        text = fixture.read_text()
        if check_source(text, stages[-1]) != check_source(text):
            raise RuntimeError(f"stage 3 differs on {fixture.name}")
        checked += 1
    for text in ('print(missing)', 'print(sorted([1], reverse=1))'):
        try:
            check_source(text)
        except ValueError as error:
            diagnostic = str(error)
        else:
            raise RuntimeError("negative bootstrap fixture was accepted by the shim")
        result = runner.run_binary(stages[-1], stdin=text.encode())
        if not result.code or result.stdout or diagnostic not in result.stderr.decode(errors="replace"):
            raise RuntimeError("stage 3 failed the diagnostic/no-partial-IR check")

    program = directory / "checker-smoke.wasm"
    smoke = check_source(SMOKE_SOURCE, stages[-1])
    if smoke != check_source(SMOKE_SOURCE):
        raise RuntimeError("stage 3 smoke typed IR differs from the shim")
    program.write_bytes(encode(emit_module(to_module(smoke)), debug_names=False))
    executed = runner.run_binary(program)
    if executed.code or executed.stderr or executed.stdout != b"55\n":
        raise RuntimeError(f"stage 3 checked program failed: {executed}")

    return {
        "scope": "Snek source parser/checker fixed point; Python bridge, emitter, runtime generation and encoder still required",
        "source_sha256": digest(source.encode()),
        "runtime_abi": expected["runtime_abi"],
        "body_ir_schema": expected["schema"],
        "stages": [{"file": f"checker.stage{index}.wasm", "sha256": digest(path.read_bytes()),
                    "bytes": path.stat().st_size} for index, path in enumerate(stages, 1)],
        "stage2_equals_stage3": True,
        "self_ir_parity": True,
        "fixture_ir_comparisons": checked,
        "negative_fixtures": 2,
        "smoke_output": executed.stdout.decode().strip(),
        "runtime": runner.available_runtime(),
        "optimizer": None,
        "rebuilt_debug_names": False,
    }


def bootstrap(directory: Path) -> dict:
    # Preserve previously verified artifacts if construction or proof fails.
    with tempfile.TemporaryDirectory(prefix="snek-checker-bootstrap-") as temporary:
        work = Path(temporary)
        build(work)
        source = (work / "bodies.bundle.py").read_text()
        manifest = rebuild(source, work / "bodies.wasm", work)
        directory.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(work / "bodies.wasm", directory / "checker.stage1.wasm")
        for name in ("checker.stage2.wasm", "checker.stage3.wasm", "bodies.bundle.py"):
            shutil.copyfile(work / name, directory / name)
        # The standard experimental CLI now uses the verified rebuilt checker.
        shutil.copyfile(work / "checker.stage3.wasm", directory / "bodies.wasm")
        (directory / "checker-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        return manifest


def main() -> int:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--out", type=Path, default=ROOT / "bootstrap/build")
    args = cli.parse_args()
    try:
        manifest = bootstrap(args.out)
    except (ValueError, RuntimeError, OSError, driver.CompileError, driver.ToolError) as error:
        print(f"checker bootstrap: {error}", file=sys.stderr)
        return 1
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
