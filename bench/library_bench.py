"""Warm JavaScript/Wasm boundary benchmarks; compile/startup are excluded.

python bench/library_bench.py --reps 10 --iterations 10000 --out results.json
Builds raw and optimized libraries; every measured batch checks its checksum.
The JS control is reported separately, never subtracted from timings.
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from bench import NODE, environment, save
from common import sha, stats

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from snekc import driver

EXPORTS = ["scalar", "text", "values", "mapping", "echo_list", "echo_dict", "numbers", "callback", "async_host"]


def measure(directory, *, optimize, compiler, reps, warmups, iterations):
    built = driver.build(ROOT / "bench/library_program.py", directory / "library.wasm",
                         library=True, exports=EXPORTS, compiler=compiler, optimize=optimize)
    (directory / "package.json").write_text('{"type":"module"}', encoding="utf-8")
    spec = directory / "spec.json"
    spec.write_text(json.dumps({"bindings": str(built.js_path), "wasm": str(built.wasm_path),
                               "reps": reps, "warmups": warmups, "iterations": iterations}), encoding="utf-8")
    result = subprocess.run([NODE, str(ROOT / "bench/library_bench.mjs"), str(spec)],
                            capture_output=True, text=True, encoding="utf-8", timeout=300, check=True)
    cases = json.loads(result.stdout)
    if not cases or any(len(case["samples_ns"]) != reps for case in cases.values()):
        raise ValueError("missing benchmark samples")
    for case in cases.values():
        case["ns_per_operation"] = stats(case.pop("samples_ns"))
    artifacts = {}
    for name, path in (("wasm", built.wasm_path), ("javascript", built.js_path), ("typescript", built.dts_path)):
        data = path.read_bytes()
        artifacts[name] = {"bytes": len(data), "gzip_bytes": len(gzip.compress(data, mtime=0)), "sha256": sha(data)}
    return {"cases": cases, "artifacts": artifacts}


def positive(text):
    value = int(text)
    if value <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reps", type=positive, default=10)
    parser.add_argument("--iterations", type=positive, default=10000)
    parser.add_argument("--warmups", type=positive, default=3)
    parser.add_argument("--compiler", choices=("python", "snek"), default="snek")
    parser.add_argument("--out", type=Path, default=ROOT / "bench/work/library.json")
    args = parser.parse_args(argv)
    if args.iterations > 1000000:
        parser.error("iterations must be <= 1000000")
    result = {"environment": environment(), "compiler": args.compiler,
              "reps": args.reps, "warmups": args.warmups, "iterations": args.iterations,
              "timing": "warm instance, ns/operation; compilation/instantiation/setup excluded; checksum accumulation included",
              "load_before": list(os.getloadavg()) if hasattr(os, "getloadavg") else None,
              "complete": False, "variants": {}}
    save(args.out, result)
    for optimize in (False, True):
        with tempfile.TemporaryDirectory(prefix="snek-library-bench-") as directory:
            variant = measure(Path(directory), optimize=optimize, compiler=args.compiler, reps=args.reps,
                              warmups=args.warmups, iterations=args.iterations)
        result["variants"]["optimized" if optimize else "raw"] = variant
        save(args.out, result)
    result["load_after"] = list(os.getloadavg()) if hasattr(os, "getloadavg") else None
    result["complete"] = True
    save(args.out, result)
    for mode, variant in result["variants"].items():
        for name, case in variant["cases"].items():
            data = case["ns_per_operation"]
            print(f"{mode:9} {name:25} {data['median']:10.1f} ± {data['mad']:.1f} ns/{case['unit']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
