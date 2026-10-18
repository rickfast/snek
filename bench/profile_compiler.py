"""Profile owned compiler phases in a separate, instrumented Wasm build.

The production bundle is untouched. The current native compiler builds the
probe; every probe output must match the native compiler byte-for-byte.
Internal monotonic-clock intervals exclude Wasmtime startup/JIT. Instrumentation
and its clock/stdio imports can perturb timings; use native_bench.py for A/B
performance claims. No profile output is accepted as a production compiler.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

import native_bench as nb

ROOT = nb.ROOT
from bootstrap.build_emitter import COMPILER_STEPS, ENCODE_STEPS, compiler_entry, compiler_source
from bootstrap.linker import statements


STEPS = COMPILER_STEPS + ENCODE_STEPS


def instrument(source):
    parts = statements(source)
    entries = [part for part in parts if part.words[:2] == ["def", "emitter_command"]]
    expected = statements(compiler_entry())
    if (len(entries) != 1 or entries[0].words != expected[0].words
            or parts[-1].words != ["emitter_command", "(", ")"]):
        raise ValueError("compiler pipeline changed; review profiling instrumentation")
    prefix = "".join(part.source for part in parts[:-1] if part is not entries[0])
    result = prefix + "\nfrom snek import time, f64\n\ndef profile_command() -> None:\n"
    result += "    component = False\n"
    result += '    exports: list[str] = []\n    entry = ""\n'
    result += "    marks: list[f64] = [time.monotonic()]\n"
    for _, statement in STEPS:
        result += "    " + statement + "\n    marks.append(time.monotonic())\n"
    result += '    report: list[str] = []\n'
    for i, (name, _) in enumerate(STEPS):
        result += f'    report.append(\'{name}=\' + str((marks[{i + 1}] - marks[{i}]) * 1000.0))\n'
    result += '    sys.stderr.write("SNEK_PROFILE " + " ".join(report) + "\\n")\nprofile_command()\n'
    return result


def parse_profile(stderr):
    lines = stderr.splitlines()
    if len(lines) != 1 or not lines[0].startswith(b"SNEK_PROFILE "):
        raise ValueError(f"unexpected profile diagnostics: {stderr[:1000]!r}")
    pairs = [item.split(b"=") for item in lines[0].removeprefix(b"SNEK_PROFILE ").split()]
    if [key.decode() for key, _ in pairs] != [name for name, _ in STEPS]:
        raise ValueError("missing, duplicate or reordered compiler phases")
    result = {key.decode(): float(value) for key, value in pairs}
    nb.stats(list(result.values()))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=ROOT / "native/target/release/snek")
    parser.add_argument("--runtime", default="wasmtime")
    parser.add_argument("--collector", choices=("drc", "copying"), default="drc")
    parser.add_argument("--reps", type=int, default=5)
    parser.add_argument("--self-reps", type=int, default=3)
    parser.add_argument("--only", default="tiny,medium,large,self")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    names = args.only.split(",")
    if args.reps < 1 or args.self_reps < 1 or set(names) - {"tiny", "medium", "large", "self"}:
        parser.error("invalid workload or repetition count")
    if args.out.exists():
        parser.error("--out already exists; choose a new snapshot path")
    binary = args.binary.resolve()
    runtime = shutil.which(args.runtime)
    if not runtime:
        parser.error("Wasmtime CLI is required")
    source = compiler_source()
    probe = instrument(source)
    result = {
        "complete": False, "started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "environment": nb.bench.environment(), "source_sha256": nb.source_identity(),
        "compiler_source_sha256": nb.sha(source.encode()), "probe_source_sha256": nb.sha(probe.encode()),
        "binary_sha256": nb.sha(binary.read_bytes()),
        "runtime": subprocess.check_output([runtime, "--version"], text=True).strip(),
        "runtime_args": ["run", "-C", "collector=" + args.collector],
        "method": "fresh Wasmtime CLI processes; internal monotonic-clock intervals exclude JIT; one discarded warmup; byte equality checked on every trial",
        "reps": args.reps, "self_reps": args.self_reps, "load_average_start": os.getloadavg(),
        "measurements": {},
    }

    def save():
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, indent=2) + "\n")

    save()
    try:
        with tempfile.TemporaryDirectory(prefix="snek-profile-") as directory:
            work = Path(directory)
            probe_source, probe_wasm = work / "profile.py", work / "profile.wasm"
            probe_source.write_text(probe)
            subprocess.run([binary, "build", probe_source, "-o", probe_wasm], check=True, timeout=180)
            result["probe_wasm_sha256"] = nb.sha(probe_wasm.read_bytes())
            inputs = {"tiny": "print(42)\n", "medium": nb.generated_source(100),
                      "large": nb.generated_source(1000), "self": source}
            for name in names:
                src, output = work / f"{name}.py", work / f"{name}.wasm"
                src.write_text(inputs[name])
                subprocess.run([binary, "build", src, "-o", output], check=True, timeout=180)
                expected = output.read_bytes()
                samples = []
                for i in range(1 + (args.self_reps if name == "self" else args.reps)):
                    trial = subprocess.run([runtime, *result["runtime_args"], probe_wasm], input=inputs[name].encode(),
                                           capture_output=True, timeout=180)
                    if trial.returncode or trial.stdout != expected:
                        raise RuntimeError(f"{name}: profile output differs: {trial.stderr[:1000]!r}")
                    sample = parse_profile(trial.stderr)
                    if i:
                        samples.append(sample)
                result["measurements"][name] = {
                    "source_sha256": nb.sha(inputs[name].encode()), "output_sha256": nb.sha(expected),
                    "phases_ms": {key: nb.stats([s[key] for s in samples]) for key in samples[0]},
                    "total_ms": nb.stats([sum(s.values()) for s in samples]),
                }
                save()
                print(name, {k: round(v["median"], 2) for k, v in result["measurements"][name]["phases_ms"].items()}, flush=True)
        if nb.source_identity() != result["source_sha256"]:
            raise RuntimeError("source changed during profile")
        result["complete"] = True
    except Exception as error:
        result["error"] = str(error)
        raise
    finally:
        result["load_average_end"] = os.getloadavg()
        save()


if __name__ == "__main__":
    main()
