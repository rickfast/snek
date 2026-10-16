"""Build a seed, then verify the source-to-binary compiler without host passes.

Only seed construction uses the Python IR bridge/emitter. Every rebuild after
that is a single invocation of the preceding Wasm compiler on unchanged source.
Verified artifacts are published only after the fixed point and smoke gate.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
from contextlib import ExitStack
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bootstrap.build_emitter import compiler_source
from bootstrap.build_bodies import SMOKE_SOURCE
from snekc import runner


def compile_guest(binary: Path, source: str) -> bytes:
    result = runner.run_binary(binary, stdin=source.encode())
    if result.code or result.stderr or not result.stdout.startswith(b"\0asm\1\0\0\0"):
        raise RuntimeError(f"{binary.name} compilation failed: {result.stderr.decode(errors='replace')} {result.stdout[:80]!r}")
    return result.stdout


def seed(source: str, output: Path) -> None:
    from bootstrap.body_bridge import check_source, to_module
    from snekc.emit_wat import emit_module
    from snekc.wasm import encode
    checked = check_source(source)
    output.write_bytes(encode(emit_module(to_module(checked)), debug_names=False))


def bootstrap(directory: Path) -> dict:
    source = compiler_source()
    with tempfile.TemporaryDirectory(prefix="snek-compiler-bootstrap-") as temporary:
        work = Path(temporary)
        (work / "compiler.bundle.py").write_text(source)
        stages = [work / f"compiler.stage{i}.wasm" for i in range(1, 4)]
        print("Building seed", flush=True)
        seed(source, stages[0])
        with ExitStack() as guards:
            for target in ("ast.parse", "snekc.check.Checker.run", "bootstrap.body_bridge.check_source",
                           "bootstrap.body_bridge.to_module", "snekc.emit_wat.emit_module", "snekc.wasm.encode"):
                guards.enter_context(patch(target, side_effect=AssertionError("host compiler fallback: " + target)))
            for index in range(1, 3):
                print(f"Compiling stage {index + 1} with stage {index}", flush=True)
                stages[index].write_bytes(compile_guest(stages[index - 1], source))
        if stages[1].read_bytes() != stages[2].read_bytes():
            raise RuntimeError("compiler stage 2 and stage 3 differ")
        smoke = work / "smoke.wasm"
        smoke.write_bytes(compile_guest(stages[-1], SMOKE_SOURCE))
        result = runner.run_binary(smoke)
        if result.code or result.stderr or result.stdout != b"55\n":
            raise RuntimeError(f"rebuilt compiler smoke failed: {result}")
        for bad in ("print(missing)", "print(sorted([1], reverse=1))"):
            result = runner.run_binary(stages[-1], stdin=bad.encode())
            if not result.code or result.stdout or b"ValueError: line " not in result.stderr:
                raise RuntimeError("rebuilt compiler did not fail closed on invalid source")
        manifest = {
            "scope": "source-to-core-Wasm compiler fixed point",
            "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
            "stage2_equals_stage3": True,
            "host_passes_after_seed": False,
            "host_fallback_guards": True,
            "negative_fixtures": 2,
            "runtime": runner.available_runtime(),
            "smoke_output": "55",
            "stages": [{"file": p.name, "bytes": p.stat().st_size,
                        "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in stages],
        }
        directory.mkdir(parents=True, exist_ok=True)
        for p in [*stages, work / "compiler.bundle.py"]:
            shutil.copyfile(p, directory / p.name)
        shutil.copyfile(stages[-1], directory / "compiler.wasm")
        (directory / "compiler-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        return manifest


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--out", type=Path, default=ROOT / "bootstrap/build")
    args = cli.parse_args()
    print(json.dumps(bootstrap(args.out), indent=2))
