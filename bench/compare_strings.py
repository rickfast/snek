"""Compare a preserved strings core module with a fresh build on Wasmtime.

  cp bench/work/strings.snek.wasm bench/work/strings.before.wasm
  # Make the compiler change, then:
  python bench/compare_strings.py --before bench/work/strings.before.wasm

The Python-hosted row uses the existing warm-module benchmark methodology.
Separate CLI rows pin DRC/copying and include process startup and uncached compile.
"""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import shutil
import statistics
import subprocess
import time

import bench


def measure(run, expected: str, reps: int) -> dict:
    times = []
    for _ in range(reps):
        result = run()
        if not bench.valid_sample({**result, "time": result["wall"]}, expected):
            raise RuntimeError(f"strings output mismatch: {result}")
        times.append(result["wall"])
    return {"ok": True, "times": times, "median_ms": bench.median_ms(times)}


def cli_run(path: Path, collector: str) -> dict:
    start = time.perf_counter()
    p = subprocess.run([bench.WASMTIME, "run", "-C", "cache=n", "-C", f"collector={collector}", str(path)],
                       capture_output=True, text=True)
    return {"code": p.returncode, "out": p.stdout, "err": p.stderr, "wall": time.perf_counter() - start}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--before", type=Path, required=True)
    ap.add_argument("--reps", type=int, default=5)
    ap.add_argument("--out", type=Path, default=bench.WORK / "strings-comparison.json")
    ns = ap.parse_args()
    if ns.reps < 1:
        ap.error("--reps must be at least 1")
    before = ns.before.resolve()
    if before == (bench.WORK / "strings.snek.wasm").resolve():
        ap.error("copy the old module first: the build overwrites work/strings.snek.wasm")
    before_bytes = before.read_bytes()
    if before_bytes[:8] != b"\0asm\1\0\0\0":
        ap.error("--before must be a core Wasm module")
    bench.WORK.mkdir(exist_ok=True)
    expected = bench.reference("strings")
    after = bench.snek_wasm("strings")
    paths = {"before": before, "after": after}
    result = {"environment": bench.environment(), "benchmark": "strings", "reps": ns.reps,
              "complete": False, "skipped": {}, "modules": {}, "measurements": {}}
    for name, path in paths.items():
        data = path.read_bytes()
        result["modules"][name] = {"file": path.name, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
    bench.save(ns.out, result)
    modes = {
        "wasmtime-py": ("instantiate + _start; module compiled once, compilation excluded",
                        "Python package default; collector is not exposed by this binding",
                        lambda path: bench.wasi_run(path, ["strings"])),
        "wasmtime-cli-drc": ("fresh CLI + uncached compile + instantiate + _start", "drc",
                             lambda path: cli_run(path, "drc")),
        "wasmtime-cli-copying": ("fresh CLI + uncached compile + instantiate + _start", "copying",
                                 lambda path: cli_run(path, "copying")),
    }
    for name, (timing, collector, run) in modes.items():
        why = bench.missing("snek-wasmtime") if name == "wasmtime-py" else (
            None if shutil.which(bench.WASMTIME) else "Wasmtime CLI not installed")
        if why:
            result["skipped"][name] = why
            continue
        row = {"timing": timing, "collector": collector}
        # Interleave old/new to reduce temporal drift. Include the first run;
        # Wasmtime compilation occurs outside wasi_run's measured interval.
        samples = {key: [] for key in paths}
        for _ in range(ns.reps):
            for key, path in paths.items():
                samples[key].extend(measure(lambda: run(path), expected, 1)["times"])
        for key, times in samples.items():
            row[key] = {"ok": True, "times": times, "median_ms": bench.median_ms(times)}
        row["speedup"] = statistics.median(samples["before"]) / statistics.median(samples["after"])
        result["measurements"][name] = row
        print(f"{name}: {row['before']['median_ms']:.1f} -> {row['after']['median_ms']:.1f} ms "
              f"({row['speedup']:.2f}x)", flush=True)
        bench.save(ns.out, result)
    result["complete"] = True
    bench.save(ns.out, result)


if __name__ == "__main__":
    main()
