"""Standalone compiler, launcher and generated-code benchmarks (macOS/Linux).

Fresh processes, one discarded warmup, interleaved routes, checked outputs.
Use --sections to select startup,compile,run,packages,compute. Package tests are
offline local-archive tests, not public-registry download/install measurements.
Use --baseline OLD_BINARY --sections compile,run for interleaved native A/B runs.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import zipfile

import bench

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from snekc import driver


def sha(data):
    return hashlib.sha256(data).hexdigest()


def stats(values):
    if not values or any(not math.isfinite(v) or v < 0 for v in values):
        raise ValueError("invalid measurement")
    median = statistics.median(values)
    return {"median": median, "mad": statistics.median(abs(v - median) for v in values),
            "min": min(values), "max": max(values), "samples": values}


def process(command, *, cwd=ROOT, env=None):
    """wait4 measures this child, not the cumulative high-water mark of all children.

    ru_maxrss is bytes on macOS, KiB on Linux. For launcher trees it is the OS's
    child high-water statistic, not a sum of simultaneously resident processes.
    File creation and output parsing are outside the wall-time interval.
    """
    if not hasattr(os, "wait4") or sys.platform not in ("darwin", "linux"):
        raise RuntimeError("wall/RSS harness currently requires macOS or Linux")
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        start = time.perf_counter()
        with subprocess.Popen(list(map(str, command)), cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                              stdout=stdout, stderr=stderr) as child:
            _, status, usage = os.wait4(child.pid, 0)
            child.returncode = os.waitstatus_to_exitcode(status)
            elapsed = (time.perf_counter() - start) * 1000
        stdout.seek(0)
        stderr.seek(0)
        return {"wall_ms": elapsed, "peak_rss_mib": usage.ru_maxrss / (1024 ** 2 if sys.platform == "darwin" else 1024),
                "code": child.returncode, "stdout": stdout.read(), "stderr": stderr.read()}


def validate(result, expected=None):
    if result["code"] != 0 or (expected is not None and result["stdout"] != expected):
        raise RuntimeError(f"invalid sample: exit={result['code']} stdout={result['stdout'][:300]!r} stderr={result['stderr'][-1000:]!r}")


def sample(result):
    out = {key: result[key] for key in ("wall_ms", "peak_rss_mib")}
    timing = [line.removeprefix(b"SNEK_TIMINGS ") for line in result["stderr"].splitlines()
              if line.startswith(b"SNEK_TIMINGS ")]
    if timing:
        if len(timing) != 1:
            raise RuntimeError("duplicate timing reports")
        out["phases_ms"] = json.loads(timing[0])
        stats(list(out["phases_ms"].values()))  # reject NaN/negative phase values
    return out


def summarize(samples):
    result = {key: stats([s[key] for s in samples]) for key in ("wall_ms", "peak_rss_mib")}
    if "phases_ms" in samples[0]:
        result["phases_ms"] = {key: stats([s["phases_ms"][key] for s in samples])
                               for key in samples[0]["phases_ms"]}
    return result


def interleaved(routes, reps):
    """Warm each route once; rotate the route order each measured repetition."""
    for run in routes.values():
        run()
    samples = {name: [] for name in routes}
    names = list(routes)
    for iteration in range(reps):
        order = names[iteration % len(names):] + names[:iteration % len(names)]
        for name in order:
            samples[name].append(sample(routes[name]()))
    return {name: summarize(values) for name, values in samples.items()}


def source_identity():
    paths = subprocess.check_output(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=ROOT).split(b"\0")
    digest = hashlib.sha256()
    for name in sorted(set(paths)):
        if not name or name.startswith(b"bench/results/"):
            continue
        path = ROOT / os.fsdecode(name)
        if path.is_file() and path.suffix in (".py", ".rs", ".toml", ".lock", ".cjs", ".mjs", ".wat", ".wit"):
            digest.update(name + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


def generated_source(count):
    functions = [f"def bench_fn_{i}(x: i32) -> i32:\n    return x + {i}\n" for i in range(count)]
    calls = [f"    value = bench_fn_{i}(value)" for i in range(count)]
    return "from snek import i32\n\n" + "\n".join(functions) + "\ndef main() -> None:\n    value = 0\n" + "\n".join(calls) + "\n    print(value)\n\nmain()\n"


def verify_compiler_hashes(hashes):
    peer = "baseline" if "baseline" in hashes else "python-selfhost"
    if hashes["native"] != hashes[peer]:
        raise RuntimeError(f"native and {peer} routes emitted different binaries")


def worker(path, reps):
    # Compilation is deliberately before the execution timer and warmup.
    bench.module(path)
    samples = [bench.wasi_run(path, [path.name]) for _ in range(reps + 1)]
    print(json.dumps({"samples": samples[1:], "warmup": samples[0]}))


class Suite:
    def __init__(self, args, work):
        self.args, self.work = args, work
        bench.WORK = work
        self.binary = args.binary.resolve()
        self.baseline = args.baseline.resolve() if args.baseline else None
        self.env = {**os.environ, "SNEK_RUNTIME": "wasmtime-py", "PYTHONIOENCODING": "utf-8"}
        self.env.pop("SNEK_COMPILER", None)
        self.env.pop("SNEK_COMPILER_WASM", None)
        self.env.pop("SNEK_FRONTEND", None)
        self.tiny = work / "tiny.py"
        self.tiny.write_text("print(42)\n")
        self.version = subprocess.check_output([self.binary, "--version"])
        self.result = {
            "complete": False, "environment": bench.environment(),
            "started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "source_sha256": source_identity(), "binary": {"sha256": sha(self.binary.read_bytes()), "bytes": self.binary.stat().st_size},
            "compiler_manifest": json.loads((ROOT / "bootstrap/build/compiler-manifest.json").read_text()),
            "reps": args.reps, "self_reps": args.self_reps, "warmups": 1,
            "method": {
                "wall": "fresh process including spawn/exit; OS file caches not flushed; routes interleaved",
                "memory": "wait4 ru_maxrss; per-child high-water statistic, not aggregate process-tree RSS; MiB",
                "phases": "native --timings; compiler_load is embedded compiler loading (AOT deserialization in current builds, JIT in older binaries); compiler_execute is the compiler's _start including its own setup and source-to-Wasm work",
                "compute": "warm compiled module; instantiate + _start; first run discarded; worker peak RSS includes JIT and all iterations",
                "packages": "unpublished local archives; offline installs; cached launches; public registry/network costs unmeasured",
                "native_runtime": "Wasmtime 49.0.2, DRC; pinned native/Cargo.lock",
            },
            "measurements": {},
        }
        self.result["load_average_start"] = os.getloadavg()
        if self.baseline:
            self.result["baseline_binary"] = {"sha256": sha(self.baseline.read_bytes()),
                                               "bytes": self.baseline.stat().st_size}
        self.result["package_tools"] = {
            name: subprocess.check_output([shutil.which(name), "--version"], text=True).strip()
            if shutil.which(name) else None for name in ("npm", "bun", "uv")
        }

    def save(self):
        self.args.out.parent.mkdir(parents=True, exist_ok=True)
        self.args.out.write_text(json.dumps(self.result, indent=2) + "\n")

    def record(self, key, data):
        self.result["measurements"][key] = data
        self.save()
        print(key, json.dumps(data)[:300] if not isinstance(data, dict) else "done", flush=True)

    def command(self, command, *, expected=None, env=None, cwd=ROOT):
        result = process(command, env=self.env if env is None else env, cwd=cwd)
        validate(result, expected)
        return result

    def startup(self):
        self.record("startup", interleaved({
            "native-version": lambda: self.command([self.binary, "--version"], expected=self.version),
            "native-help": lambda: self.command([self.binary, "--help"]),
            "python-launcher-help": lambda: self.command([sys.executable, "-m", "snekc", "--help"]),
        }, self.args.reps))

    def compile(self):
        sources = {"tiny": self.tiny}
        for name, count in (("medium", 100), ("large", 1000)):
            sources[name] = self.work / f"{name}.py"
            sources[name].write_text(generated_source(count))
        sources["self"] = ROOT / "bootstrap/build/compiler.bundle.py"
        for name, source in sources.items():
            variants = {"native": [self.binary, "--timings", "build", source],
                        "python-selfhost": [sys.executable, "-m", "snekc", "build", source, "--compiler", "snek", "--no-opt"]}
            if name != "self":
                variants["python-reference"] = [sys.executable, "-m", "snekc", "build", source, "--compiler", "python", "--no-opt"]
            if self.baseline:
                variants = {"native": variants["native"],
                            "baseline": [self.baseline, "--timings", "build", source]}
            routes, hashes = {}, {}
            expected = None if name == "self" else subprocess.check_output([sys.executable, source], env={**self.env, "PYTHONPATH": str(ROOT)})
            for route, command in variants.items():
                output = self.work / f"{name}-{route}.wasm"
                self.command([*command, "-o", output])
                hashes[route] = sha(output.read_bytes())
                if name == "self":
                    assert output.read_bytes() == (ROOT / "bootstrap/build/compiler.stage3.wasm").read_bytes()
                else:
                    got = bench.wasi_run(output, [name])
                    if got["code"] or got["out"].encode() != expected:
                        raise RuntimeError(f"compiled {route}/{name} has incorrect behavior: {got}")

                def trial(command=command, output=output, digest=hashes[route]):
                    output.unlink()
                    result = self.command([*command, "-o", output])
                    if sha(output.read_bytes()) != digest:
                        raise RuntimeError("compiler output changed between trials")
                    return result
                routes[route] = trial
            verify_compiler_hashes(hashes)
            self.record("compile/" + name, {"source_bytes": source.stat().st_size,
                        "source_lines": len(source.read_text().splitlines()), "source_sha256": sha(source.read_bytes()),
                        "output_sha256": hashes,
                        "routes": interleaved(routes, self.args.self_reps if name == "self" else self.args.reps)})

    def run(self):
        for name, source in {"tiny": self.tiny, "strings": bench.PROGRAMS / "strings.py"}.items():
            expected = subprocess.check_output([sys.executable, source], env={**self.env, "PYTHONPATH": str(ROOT)})
            routes = {"native": [self.binary, "--timings", "run", source],
                      "python-selfhost": [sys.executable, "-m", "snekc", "run", source, "--compiler", "snek", "--no-opt"],
                      "python-reference": [sys.executable, "-m", "snekc", "run", source, "--compiler", "python", "--no-opt"],
                      "cpython": [sys.executable, source]}
            if self.baseline:
                routes = {"native": routes["native"],
                          "baseline": [self.baseline, "--timings", "run", source]}
            env = {**self.env, "PYTHONPATH": os.pathsep.join([str(ROOT), self.env.get("PYTHONPATH", "")])}
            self.record("run/" + name, interleaved({route: lambda command=command: self.command(command, expected=expected, env=env)
                                                    for route, command in routes.items()}, self.args.reps))

    def packages(self):
        dist = self.args.dist.resolve()
        wheels = list(dist.glob("*.whl"))
        platforms = list(dist.glob("snek-lang-*-*.tgz"))
        if len(wheels) != 1 or len(platforms) != 1:
            raise RuntimeError("--dist must contain exactly one platform's distributions")
        with zipfile.ZipFile(wheels[0]) as wheel:
            bundled = wheel.read(next(n for n in wheel.namelist() if n.startswith("snek_cli/bin/")))
        if sha(bundled) != self.result["binary"]["sha256"]:
            raise RuntimeError("rebuild packages: bundled executable differs from benchmark executable")
        npm, npx, bun, uv = [shutil.which(name) for name in ("npm", "npx", "bun", "uv")]
        if not all((npm, npx, bun, uv)):
            raise RuntimeError("package benchmarks require npm/npx, Bun and uv")
        env = {**self.env, "npm_config_cache": str(self.work / "npm-cache"), "UV_CACHE_DIR": str(self.work / "uv-cache"),
               "UV_TOOL_DIR": str(self.work / "uv-tools"), "UV_TOOL_BIN_DIR": str(self.work / "uv-bin"),
               "BUN_INSTALL_CACHE_DIR": str(self.work / "bun-cache")}
        install = self.command([npm, "install", "--offline", "--ignore-scripts", "--omit=optional", "--no-audit", "--no-fund",
                                *sorted(dist.glob("*.tgz"))], env=env, cwd=self.work)
        commands = {"direct": [self.binary], "npx": [npx, "--offline", "snek-lang"],
                    "bunx": [bun, "x", "--bun", "--no-install", "snek-lang"],
                    "uvx": [uv, "tool", "run", "--offline", "--python", sys.executable, "--from", wheels[0], "snek-lang"]}
        first_uv = self.command([*commands["uvx"], "--version"], expected=self.version, env=env, cwd=self.work)
        self.record("packages/local-first-use", {"npm-install-only": sample(install), "uvx-install-and-version": sample(first_uv),
                    "note": "one observation each; local archives/no network; Bun shares the npm-installed local tree, so no Bun install time measured"})
        for workload, args, expected in (("version", ["--version"], self.version), ("run-tiny", ["run", self.tiny], b"42\n")):
            self.record("packages/" + workload, interleaved({name: lambda cmd=cmd: self.command([*cmd, *args], expected=expected, env=env, cwd=self.work)
                                                               for name, cmd in commands.items()}, self.args.reps))

    def compute(self):
        for name in bench.BENCHES:
            source = bench.PROGRAMS / f"{name}.py"
            expected = bench.reference(name)
            raw, optimized = self.work / f"{name}-raw.wasm", self.work / f"{name}-O3.wasm"
            self.command([self.binary, "build", source, "-o", raw])
            self.command([*driver.find_wasm_opt(), raw, "-o", optimized, *driver.WASM_FEATURES, "-O3", "--closed-world"])
            rows = {}
            for mode, binary in (("raw", raw), ("O3", optimized)):
                wt = self.command([sys.executable, __file__, "--worker", binary, "--reps", str(self.args.reps)])
                samples = json.loads(wt["stdout"])
                for item in [samples["warmup"], *samples["samples"]]:
                    if item["code"] or item["out"] != expected or item["err"]:
                        raise RuntimeError(f"incorrect Wasmtime compute result: {item}")
                rows["wasmtime-" + mode] = {"execute_ms": stats([s["wall"] * 1000 for s in samples["samples"]]), "worker_peak_rss_mib": wt["peak_rss_mib"]}
                spec = self.work / "node-spec.json"
                spec.write_text(json.dumps({"kind": "snek-v8", "reps": self.args.reps + 1, "jobs": [{"name": name, "wasm": str(binary), "expected": expected}]}))
                node = self.command([bench.NODE, "--no-warnings", ROOT / "bench/node_bench.mjs", spec])
                job = json.loads(node["stdout"])["results"][name]
                if not job["ok"] or len(job["times"]) != self.args.reps + 1:
                    raise RuntimeError(f"incorrect V8 compute result: {job}")
                rows["v8-" + mode] = {"execute_ms": stats([t * 1000 for t in job["times"][1:]]), "worker_peak_rss_mib": node["peak_rss_mib"]}
                rows[mode + "-module"] = {"bytes": binary.stat().st_size, "sha256": sha(binary.read_bytes())}
            script = self.work / "flat.py"
            script.write_text(bench.flat_source(name))
            times, memory = [], []
            for iteration in range(self.args.reps + 1):
                result = self.command([sys.executable, script])
                body, elapsed = bench.split_time(result["stdout"].decode())
                if body != expected or elapsed is None:
                    raise RuntimeError("incorrect CPython compute result")
                if iteration:
                    times.append(elapsed * 1000)
                    memory.append(result["peak_rss_mib"])
            rows["cpython"] = {"execute_ms": stats(times), "peak_rss_mib": stats(memory)}
            self.record("compute/" + name, rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=ROOT / "native/target/release/snek")
    parser.add_argument("--baseline", type=Path, help="compare with a saved native binary (compile,run sections only)")
    parser.add_argument("--dist", type=Path, default=ROOT / "dist")
    parser.add_argument("--reps", type=int, default=5)
    parser.add_argument("--self-reps", type=int, default=3)
    parser.add_argument("--sections", default="startup,compile,run,packages,compute")
    parser.add_argument("--out", type=Path, default=ROOT / "bench/results" / f"{datetime.date.today().isoformat()}-native.json")
    parser.add_argument("--worker", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.reps < 1 or args.self_reps < 1:
        parser.error("repetition counts must be positive")
    if args.worker:
        worker(args.worker, args.reps)
        return
    sections = args.sections.split(",")
    if not sections or set(sections) - {"startup", "compile", "run", "packages", "compute"}:
        parser.error("unknown benchmark section")
    if args.baseline and set(sections) - {"compile", "run"}:
        parser.error("--baseline requires --sections compile,run (or either section)")
    # Do not overwrite a historical benchmark snapshot accidentally.
    if args.out.exists():
        parser.error("--out already exists; choose a new snapshot path")
    with tempfile.TemporaryDirectory(prefix="snek-native-bench-") as directory:
        suite = Suite(args, Path(directory))
        suite.save()
        try:
            for section in sections:
                getattr(suite, section)()
            if source_identity() != suite.result["source_sha256"]:
                raise RuntimeError("source changed during benchmark")
            suite.result["complete"] = True
        except Exception as error:
            suite.result["error"] = str(error)
            raise
        finally:
            suite.result["load_average_end"] = os.getloadavg()
            suite.save()
    print(f"Saved {args.out}")


if __name__ == "__main__":
    main()
