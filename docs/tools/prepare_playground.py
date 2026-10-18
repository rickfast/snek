"""Stage verified compiler assets for the static playground; never publish a stale image."""
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from bootstrap.build_emitter import compiler_source


def prepare():
    build = ROOT / "bootstrap/build"
    manifest = json.loads((build / "compiler-manifest.json").read_text())
    source = compiler_source().encode()
    binary = (build / "compiler.wasm").read_bytes()
    if manifest["source_sha256"] != hashlib.sha256(source).hexdigest():
        raise RuntimeError("Stale compiler: run python bootstrap/bootstrap_compiler.py")
    if not manifest["stage2_equals_stage3"] or not manifest["host_fallback_guards"] or manifest["host_passes_after_seed"]:
        raise RuntimeError("Compiler is missing its fixed-point proof")
    for stage in manifest["stages"]:
        data = (build / stage["file"]).read_bytes()
        if hashlib.sha256(data).hexdigest() != stage["sha256"]:
            raise RuntimeError("Compiler stage checksum does not match the manifest")
    if binary != (build / "compiler.stage2.wasm").read_bytes() or binary != (build / "compiler.stage3.wasm").read_bytes():
        raise RuntimeError("Compiler does not match the verified fixed point")
    target = ROOT / "docs/assets/playground/generated"
    target.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(build / "compiler.wasm", target / "compiler.wasm")
    # Reuse the library's executable feature probes without a second implementation.
    shutil.copyfile(ROOT / "snekc/library.js", target / "bindings.js")
    for name in ("hello", "word-count", "streams"):
        shutil.copyfile(ROOT / "docs/examples" / (name + ".snek"), target / (name + ".snek"))
    print(f"Staged verified playground compiler ({len(binary):,} bytes)")


if __name__ == "__main__":
    prepare()
