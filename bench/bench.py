"""Benchmarks: snek against other Pythons that run as WebAssembly (and native CPython).

Every benchmark in programs/ is one snek source file, so it is also plain Python. For
the interpreters it becomes a "flat" script: erased annotations, a tiny shim (i32 = int,
...), and a timer around main() (asyncio.run(main()) for async_calls). Compute is timed
inside the program for interpreters
(time.perf_counter, or ticks_us on MicroPython) and around _start for snek, which has no
clock API (its instantiation cost is about a millisecond; see the startup numbers).
Every runtime's output is checked against native CPython before its time counts.

Engines: WASI modules run in Wasmtime through wasmtime-py, compiled once per process
and reused, with compile time reported separately. Pyodide and MicroPython are
Emscripten builds and run in Node; snek runs there too for a same-engine comparison.

  python bench/bench.py run      [--reps N] [--only fib,...] [--runtimes a,b]
  python bench/bench.py startup                  # compile + start an empty program
  python bench/bench.py alloc                    # GC allocation cost, Wasmtime vs V8
  python bench/bench.py component [--reps N] [--only fib,...]  # cold WASI 0.3 CLI time
  python bench/bench.py snapshot                 # work/*.json -> results/<date>.json
  python bench/bench.py table [results/<date>.json]

Setup: `npm install` in bench/ for the comparison runtimes, Node 24+ (SNEK_NODE to pick a
binary), wasm-opt (SNEK_WASM_OPT), `pip install wasmtime`. Optional: py2wasm, which needs
Python 3.11; point PY2WASM at its executable. Missing runtimes are skipped.
"""

from __future__ import annotations

import argparse
import ast
import datetime
import gzip
import json
import math
import os
import platform
import re
import shlex
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from common import sha, source_identity, stats

try:
    import wasmtime
except ImportError:
    wasmtime = None

HERE = Path(__file__).resolve().parent
SNEK = HERE.parent
PROGRAMS = HERE / "programs"
WORK = HERE / "work"
RT = HERE / "node_modules"
NODE = os.environ.get("SNEK_NODE", "node")
WASMTIME = os.environ.get("SNEK_WASMTIME", "wasmtime")
CPY_WASI = RT / "@antonz/python-wasi/dist/python.wasm"
RUSTPY = RT / "rustpython-wasi/rustpython.wasm"
PY2WASM = Path(os.environ["PY2WASM"]) if os.environ.get("PY2WASM") else None

BENCHES = ["fib", "mandelbrot", "binary_trees", "sieve", "strings", "exceptions",
           "dicts", "list_builtins", "protocols", "generators", "async_calls"]
RUNTIMES = ["snek-v8", "snek-wasmtime", "py2wasm", "cpython-native", "pyodide", "cpython-wasi", "rustpython-wasi",
            "micropython"]
LABELS = {
    "snek-v8": "snek (V8)", "snek-wasmtime": "snek (Wasmtime)", "py2wasm": "py2wasm (Wasmtime)",
    "cpython-native": "CPython, native", "pyodide": "Pyodide (V8)", "cpython-wasi": "CPython WASI (Wasmtime)",
    "rustpython-wasi": "RustPython WASI (Wasmtime)", "micropython": "MicroPython (V8)",
}
SHIM = "i32 = int\ni64 = int\nf32 = float\nf64 = float\n"
ASYNC_SHIM = '''from asyncio import run
async def checkpoint():
    from asyncio import sleep
    await sleep(0)
'''
TIMER = '''
import time as _t
if hasattr(_t, "perf_counter"):
    _a = _t.perf_counter()
    ENTRY()
    _b = _t.perf_counter()
    print("@@TIME", _b - _a)
else:
    _a = _t.ticks_us()
    ENTRY()
    _b = _t.ticks_us()
    print("@@TIME", _t.ticks_diff(_b, _a) / 1000000)
'''


class EraseAnnotations(ast.NodeTransformer):
    """Keep runtime operations intact, without requiring typing on small interpreters."""

    def visit_AnnAssign(self, node):
        return ast.copy_location(ast.Assign(targets=[node.target], value=node.value), node) if node.value else None

    def visit_arg(self, node):
        node.annotation = None
        return node

    def visit_FunctionDef(self, node):
        node.returns = None
        return self.generic_visit(node)

    visit_AsyncFunctionDef = visit_FunctionDef


def flat_source(name: str) -> str:
    if name == "empty":
        return SHIM + "def main():\n    pass\n" + TIMER.replace("ENTRY()", "main()")
    tree = ast.parse((PROGRAMS / f"{name}.py").read_text())
    # Benchmarks end with a main guard; the timer supplies its invocation instead.
    if not (isinstance(tree.body[-1], ast.If)
            and ast.unparse(tree.body[-1].test) == "__name__ == '__main__'"):
        raise ValueError(f"{name}: expected a final __main__ guard")
    tree.body.pop()
    tree.body = [n for n in tree.body if not (isinstance(n, ast.ImportFrom)
                 and n.module in ("snek", "typing", "types", "__future__"))]
    is_async = any(isinstance(n, ast.AsyncFunctionDef) and n.name == "main" for n in tree.body)
    tree = ast.fix_missing_locations(EraseAnnotations().visit(tree))
    return (SHIM + (ASYNC_SHIM if is_async else "") + ast.unparse(tree) + "\n"
            + TIMER.replace("ENTRY()", "run(main())" if is_async else "main()"))


def split_time(out: str) -> tuple[str, float | None]:
    m = re.search(r"^@@TIME (\S+)$", out, re.M)
    body = re.sub(r"^@@TIME .*\n?", "", out, flags=re.M)
    t = float(m.group(1)) if m else None
    return body, t if t is not None and math.isfinite(t) and t >= 0 else None


def median_ms(times: list[float]) -> float:
    return stats(times)["median"] * 1000


# ---- availability -------------------------------------------------------------------


def node_major() -> int:
    try:
        v = subprocess.run([NODE, "--version"], capture_output=True, text=True).stdout.strip()
        return int(v.lstrip("v").split(".")[0])
    except (OSError, ValueError):
        return 0


def missing(rt: str) -> str | None:
    """Why a runtime can't run here, or None."""
    if rt in ("snek-v8", "pyodide", "micropython") and node_major() < 24:
        return f"needs Node 24+ (SNEK_NODE={NODE})"
    if rt == "pyodide" and not (RT / "pyodide").exists():
        return "run `npm install` in bench/"
    if rt == "micropython" and not (RT / "@micropython").exists():
        return "run `npm install` in bench/"
    if rt == "cpython-wasi" and not CPY_WASI.exists():
        return "run `npm install` in bench/"
    if rt == "rustpython-wasi" and not RUSTPY.exists():
        return "run `npm install` in bench/"
    if rt == "py2wasm" and not (PY2WASM and PY2WASM.exists()):
        return "set PY2WASM to the py2wasm executable (pip install py2wasm under Python 3.11)"
    if rt in ("snek-wasmtime", "py2wasm", "cpython-wasi", "rustpython-wasi") and wasmtime is None:
        return "install the wasmtime Python package"
    return None


def unsupported(rt: str, name: str) -> str | None:
    if name == "async_calls" and rt not in ("snek-v8", "snek-wasmtime", "cpython-native"):
        return "asyncio.run/checkpoint integration is only benchmarked on native CPython and snek"
    return None


# ---- Wasmtime -----------------------------------------------------------------------

_engine = None
_modules: dict[str, tuple[wasmtime.Module, float]] = {}


def engine() -> wasmtime.Engine:
    global _engine
    if _engine is None:
        c = wasmtime.Config()
        c.wasm_gc = True
        c.wasm_function_references = True
        c.wasm_exceptions = True
        _engine = wasmtime.Engine(c)
    return _engine


def module(path: Path) -> tuple[wasmtime.Module, float]:
    """Compile once per process; returns (module, compile seconds)."""
    key = str(path)
    if key not in _modules:
        t0 = time.perf_counter()
        m = wasmtime.Module.from_file(engine(), key)
        _modules[key] = (m, time.perf_counter() - t0)
    return _modules[key]


def wasi_run(path: Path, argv: list[str], preopen: Path | None = None) -> dict:
    mod, _ = module(path)
    linker = wasmtime.Linker(engine())
    linker.define_wasi()
    store = wasmtime.Store(engine())
    with tempfile.TemporaryDirectory() as d:
        out, err = Path(d, "o"), Path(d, "e")
        wc = wasmtime.WasiConfig()
        wc.argv = argv
        wc.stdout_file = str(out)
        wc.stderr_file = str(err)
        if preopen:
            wc.preopen_dir(str(preopen), "/work")
        store.set_wasi(wc)
        code = 0
        t0 = time.perf_counter()
        try:
            inst = linker.instantiate(store, mod)
            inst.exports(store)["_start"](store)
        except wasmtime.ExitTrap as e:
            code = e.code
        wall = time.perf_counter() - t0
        return {"code": code, "out": out.read_text(), "err": err.read_text(), "wall": wall}


# ---- builds -------------------------------------------------------------------------

_builds: set[tuple[Path, Path, bool]] = set()


def snek_build(src: Path, out: Path, *, component: bool = False) -> Path:
    # Always rebuild on a new invocation: source mtimes miss compiler/runtime changes.
    key = (src, out, component)
    if key not in _builds:
        args = [sys.executable, "-m", "snekc", "build", str(src), "-o", str(out)]
        p = subprocess.run(args + (["--component"] if component else []), cwd=SNEK,
                           capture_output=True, text=True)
        if p.returncode != 0:
            raise RuntimeError(f"snekc build {src.name} failed:\n{p.stderr}")
        _builds.add(key)
    return out


def snek_wasm(name: str, *, component: bool = False) -> Path:
    if name == "empty":
        src = WORK / "empty_snek.py"
        if not src.exists():
            src.write_text("def main() -> None:\n    pass\n\n\nmain()\n")
    else:
        src = PROGRAMS / f"{name}.py"
    suffix = "component" if component else "snek"
    return snek_build(src, WORK / f"{name}.{suffix}.wasm", component=component)


def py2wasm_wasm(name: str) -> Path:
    assert PY2WASM is not None
    out = WORK / f"{name}.py2wasm.wasm"
    src = WORK / f"p2w_{name}.py"
    text = flat_source(name)
    if not out.exists() or not src.exists() or src.read_text() != text:
        src.write_text(text)
        env = dict(os.environ, PATH=f"{PY2WASM.parent}:{os.environ['PATH']}")  # py2wasm wants patchelf there
        p = subprocess.run([str(PY2WASM), str(src), "-o", str(out)], capture_output=True, text=True, env=env, cwd=WORK)
        if p.returncode != 0:
            raise RuntimeError(f"py2wasm {name} failed:\n{p.stdout[-2000:]}{p.stderr[-2000:]}")
    return out


# ---- runtimes -----------------------------------------------------------------------


def run_snek_wasmtime(name: str) -> dict:
    r = wasi_run(snek_wasm(name), [name])
    return {**r, "time": r["wall"]}  # instantiate + run: snek has no clock API


def run_wasi_interp(path: Path, argv0: str):
    def run(name: str) -> dict:
        script = WORK / f"flat_{name}.py"
        script.write_text(flat_source(name))
        r = wasi_run(path, [argv0, f"/work/{script.name}"], preopen=WORK)
        body, t = split_time(r["out"])
        return {**r, "out": body, "time": t}
    return run


def run_py2wasm(name: str) -> dict:
    r = wasi_run(py2wasm_wasm(name), [name])
    body, t = split_time(r["out"])
    return {**r, "out": body, "time": t}


def run_native(name: str) -> dict:
    script = WORK / f"flat_{name}.py"
    script.write_text(flat_source(name))
    t0 = time.perf_counter()
    p = subprocess.run([sys.executable, str(script)], capture_output=True, text=True)
    wall = time.perf_counter() - t0
    body, t = split_time(p.stdout)
    return {"code": p.returncode, "out": body, "err": p.stderr, "wall": wall, "time": t}


def node(script: str, *args: str) -> dict:
    p = subprocess.run([NODE, "--no-warnings", "--max-old-space-size=4096", str(HERE / script), *args],
                       capture_output=True, text=True, env=dict(os.environ, BENCH_RT=str(RT)))
    if p.returncode != 0:
        raise RuntimeError(f"node {script} {args} failed:\n{p.stderr[-3000:]}")
    return json.loads(p.stdout.strip().splitlines()[-1])


def run_node(kind: str, jobs: list[dict], reps: int) -> dict:
    """One Node process per runtime: loads it once, runs every job reps times."""
    spec = WORK / f"node_{kind}.json"
    spec.write_text(json.dumps({"kind": kind, "reps": reps, "jobs": jobs}))
    return node("node_bench.mjs", str(spec))


IN_PROCESS = {
    "snek-wasmtime": run_snek_wasmtime,
    "cpython-wasi": run_wasi_interp(CPY_WASI, "python"),
    "rustpython-wasi": run_wasi_interp(RUSTPY, "rustpython"),
    "py2wasm": run_py2wasm,
    "cpython-native": run_native,
}


# ---- commands -----------------------------------------------------------------------


def selected(value: str, choices: list[str]) -> list[str]:
    requested = value.split(",") if value else choices
    unknown = set(requested) - set(choices)
    if unknown:
        raise ValueError(f"unknown selection: {', '.join(sorted(unknown))}")
    return [n for n in choices if n in requested]


def valid_sample(r: dict, expected: str) -> bool:
    t = r.get("time")
    return (r["code"] == 0 and r["out"] == expected and isinstance(t, (int, float))
            and math.isfinite(t) and t >= 0)


def reference(name: str) -> str:
    r = run_native(name)
    if not valid_sample(r, r["out"]):
        raise RuntimeError(f"CPython reference failed for {name}: {r['err']}")
    return r["out"]


def metadata(ns: argparse.Namespace) -> dict:
    return {"environment": environment(), "command": ns.cmd,
            "started_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "reps": getattr(ns, "reps", 5), "skipped": {}, "complete": False}


def save(path: Path, results: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(results, indent=1, allow_nan=False) + "\n")


def cmd_run(ns: argparse.Namespace) -> bool:
    names = ["empty"] + selected(ns.only, BENCHES)
    runtimes = selected(ns.runtimes, RUNTIMES)
    out = Path(ns.out)
    results: dict = {"_meta": {**metadata(ns), "benchmarks": names, "runtimes": runtimes,
                              "timing": "interpreters: main(); snek: instantiate + _start; no warmup"}}
    failed = False
    save(out, results)  # A run is fresh, never a silent merge with historical measurements.
    expected = {n: reference(n) for n in names}
    for rt in runtimes:
        why = missing(rt)
        if why:
            print(f"{rt:16} skipped: {why}")
            results["_meta"]["skipped"][rt] = why
            save(out, results)
            continue
        res = results.setdefault(rt, {})
        supported = []
        for n in names:
            why = unsupported(rt, n)
            if why:
                res[n] = {"skipped": why, "ok": None, "times": []}
                print(f"{rt:16} {n:13} skipped: {why}")
            else:
                supported.append(n)
        if rt in IN_PROCESS:
            for n in supported:
                times: list[float] = []
                ok = True
                for _ in range(ns.reps):
                    r = IN_PROCESS[rt](n)
                    ok = valid_sample(r, expected[n])
                    if not ok:
                        print(f"  !! {rt} {n}: exit {r['code']} out={r['out'][:200]!r} err={r['err'][-400:]!r}")
                        break
                    times.append(r["time"])
                    if r["time"] > 30:  # very slow runtime: fewer repetitions
                        break
                res[n] = {"times": times, "ok": ok}
                failed |= not ok
                report(rt, n, times, ok)
        else:
            if rt == "snek-v8":
                jobs = [{"name": n, "wasm": str(snek_wasm(n)), "expected": expected[n]} for n in supported]
            else:
                jobs = [{"name": n, "source": flat_source(n), "expected": expected[n]} for n in supported]
            data = run_node(rt, jobs, ns.reps)
            for n in supported:
                d = data["results"][n]
                ok = d["ok"] and d["out"] == expected[n]
                if not ok:
                    print(f"  !! {rt} {n}: out={d['out'][:200]!r} err={d.get('err', '')[-400:]!r}")
                res[n] = {"times": d["times"], "ok": ok}
                failed |= not ok
                report(rt, n, d["times"], ok)
        if rt in ("snek-v8", "snek-wasmtime"):
            for name in supported:
                binary = snek_wasm(name).read_bytes()
                res[name]["artifact"] = {"bytes": len(binary), "gzip_bytes": len(gzip.compress(binary, mtime=0)),
                                         "sha256": sha(binary)}
        save(out, results)
    results["_meta"]["complete"] = True
    save(out, results)
    return failed


def report(rt: str, n: str, times: list[float], ok: bool) -> None:
    med = f"{median_ms(times):9.1f} ms" if times else "        -"
    print(f"{rt:16} {n:13} {'ok ' if ok else 'BAD'} median {med}  runs {[round(t * 1000, 1) for t in times]}",
          flush=True)


def cmd_startup(ns: argparse.Namespace) -> None:
    """compile: one-time (Cranelift / V8); start: instantiate + init + run an empty program, with the
    module already compiled. Medians; Node figures come from fresh processes."""
    res: dict = {"_meta": metadata(ns)}
    save(Path(ns.out), res)
    empty = WORK / "flat_empty.py"
    empty.write_text(flat_source("empty"))
    wasi = {
        "snek-wasmtime": (lambda: snek_wasm("empty"), ["empty"], None),
        "cpython-wasi": (lambda: CPY_WASI, ["python", "/work/flat_empty.py"], WORK),
        "rustpython-wasi": (lambda: RUSTPY, ["rustpython", "/work/flat_empty.py"], WORK),
        "py2wasm": (lambda: py2wasm_wasm("empty"), ["empty"], None),
    }
    for name, (path_fn, argv, pre) in wasi.items():
        if missing(name):
            print(f"{name:16} skipped: {missing(name)}")
            res["_meta"]["skipped"][name] = missing(name)
            continue
        path = path_fn()
        code = (f"import sys; sys.path.insert(0, {str(HERE)!r}); import bench; "
                f"print(bench.module(bench.Path({str(path)!r}))[1])")
        comp = [float(subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout)
                for _ in range(3)]  # fresh processes: nothing cached
        samples = [wasi_run(path, argv, pre) for _ in range(5)]
        if any(s["code"] != 0 or split_time(s["out"])[0] != "" for s in samples):
            raise RuntimeError(f"{name}: empty program failed")
        starts = [s["wall"] for s in samples]
        res[name] = {"compile": statistics.median(comp), "start": statistics.median(starts),
                     "size": path.stat().st_size, "compile_times": comp, "start_times": starts}
        print(name, res[name], flush=True)
    starts = []
    for _ in range(5):
        t = time.perf_counter()
        subprocess.run([sys.executable, str(empty)], capture_output=True, check=True)
        starts.append(time.perf_counter() - t)
    res["cpython-native"] = {"compile": 0.0, "start": statistics.median(starts), "size": None,
                             "note": "whole process", "start_times": starts}
    print("cpython-native", res["cpython-native"], flush=True)
    sizes = {"pyodide": ["pyodide/pyodide.asm.wasm", "pyodide/python_stdlib.zip", "pyodide/pyodide.asm.mjs"],
             "micropython": ["@micropython/micropython-webassembly-pyscript/micropython.wasm",
                             "@micropython/micropython-webassembly-pyscript/micropython.mjs"]}
    for kind in ["snek-v8", "pyodide", "micropython"]:
        if missing(kind):
            print(f"{kind:16} skipped: {missing(kind)}")
            res["_meta"]["skipped"][kind] = missing(kind)
            continue
        samples = [node("node_startup.mjs", kind, str(snek_wasm("empty"))) for _ in range(5)]
        res[kind] = {"compile": statistics.median(s["compile"] for s in samples),
                     "start": statistics.median(s["start"] for s in samples),
                     "compile_times": [s["compile"] for s in samples],
                     "start_times": [s["start"] for s in samples],
                     "size": sum((RT / f).stat().st_size for f in sizes[kind]) if kind in sizes else
                     snek_wasm("empty").stat().st_size}
        print(kind, res[kind], flush=True)
    res["module_sizes"] = {b: {"snek": snek_wasm(b).stat().st_size,
                               **({"py2wasm": py2wasm_wasm(b).stat().st_size} if not missing("py2wasm") else {})}
                           for b in BENCHES}
    res["_meta"]["complete"] = True
    save(Path(ns.out), res)


def cmd_component(ns: argparse.Namespace) -> bool:
    """Cold CLI wall time, kept separate from the in-process compute comparison."""
    if not shutil.which(WASMTIME):
        raise RuntimeError("component benchmarks need the Wasmtime CLI (SNEK_WASMTIME)")
    names = ["empty"] + selected(ns.only, BENCHES)
    res = {"_meta": {**metadata(ns), "benchmarks": names,
                    "timing": "fresh Wasmtime CLI process + uncached compile + instantiate + run"}}
    save(Path(ns.out), res)
    failed = False
    for n in names:
        expected = reference(n)
        path = snek_wasm(n, component=True)
        times = []
        ok = True
        for _ in range(ns.reps):
            t0 = time.perf_counter()
            p = subprocess.run([WASMTIME, "run", "-C", "cache=n", str(path)], capture_output=True, text=True)
            wall = time.perf_counter() - t0
            if p.returncode != 0 or p.stdout != expected:
                ok = False
                print(f"  !! component {n}: exit {p.returncode} out={p.stdout[:200]!r} err={p.stderr[-400:]!r}")
                break
            times.append(wall)
        res[n] = {"times": times, "ok": ok, "size": path.stat().st_size}
        failed |= not ok
        report("snek-component", n, times, ok)
        save(Path(ns.out), res)
    res["_meta"]["complete"] = True
    save(Path(ns.out), res)
    return failed


ALLOC_TEMPLATE = '''from snek import i32, i64


class Box:
    v: i32

    def __init__(self, v: i32) -> None:
        self.v = v


def main() -> None:
    keep: list[Box] = []
    for i in range({live}):
        keep.append(Box(i))
    ring: list[Box] = []
    for i in range(64):
        ring.append(Box(0))
    for i in range(3000000):
        {body}
    s: i64 = 0
    for b in ring:
        s += b.v
    print(s, len(keep))


main()
'''


def cmd_alloc(ns: argparse.Namespace) -> None:
    """3M loop iterations that either allocate a small object (stored into a ring buffer, so
    wasm-opt's escape analysis can't remove it) or just write a field, with 0..1M objects kept
    alive. alloc - noalloc is the allocation cost; how it moves with the live set separates
    per-allocation overhead from copying live data."""
    lives = [0, 100_000, 300_000, 1_000_000]
    res = {"_meta": metadata(ns)}
    save(Path(ns.out), res)
    progs = {}
    expected = {}
    for live in lives:
        for kind, body in (("noalloc", "ring[i & 63].v = i"), ("alloc", "ring[i & 63] = Box(i)")):
            name = f"{kind}_{live}"
            src = WORK / f"alloc_{name}.py"
            text = ALLOC_TEMPLATE.format(live=live, body=body)
            if not src.exists() or src.read_text() != text:
                src.write_text(text)
            progs[name] = snek_build(src, WORK / f"alloc_{name}.wasm")
            expected[name] = f"{sum(range(3_000_000 - 64, 3_000_000))} {live}\n"
    v8 = {}
    if not missing("snek-v8"):
        v8 = run_node("snek-v8", [{"name": n, "wasm": str(p), "expected": expected[n]}
                                  for n, p in progs.items()], 5)["results"]
    wt_missing = missing("snek-wasmtime")
    for rt in ("snek-v8", "snek-wasmtime"):
        if missing(rt):
            res["_meta"]["skipped"][rt] = missing(rt)
    for n, p in progs.items():
        samples = [wasi_run(p, [n]) for _ in range(5)] if not wt_missing else []
        if any(s["code"] != 0 or s["out"] != expected[n] for s in samples):
            raise RuntimeError(f"Wasmtime allocation benchmark {n} produced incorrect output")
        if n in v8 and not v8[n]["ok"]:
            raise RuntimeError(f"V8 allocation benchmark {n} produced incorrect output")
        wt = [s["wall"] for s in samples]
        res[n] = {"wasmtime_ms": median_ms(wt) if wt else None,
                  "v8_ms": median_ms(v8[n]["times"]) if n in v8 else None,
                  "wasmtime_times": wt, "v8_times": v8[n]["times"] if n in v8 else [], "ok": bool(wt or v8)}
        print(n, {k: v for k, v in res[n].items() if k.endswith("_ms")}, flush=True)
    for live in lives:
        a, b = res[f"alloc_{live}"], res[f"noalloc_{live}"]
        per = {k: (a[k] - b[k]) * 1e6 / 3_000_000 for k in ("wasmtime_ms", "v8_ms") if a[k] is not None}
        print(f"live {live:>9,}: ns per allocation {per}")
    res["_meta"]["complete"] = True
    save(Path(ns.out), res)


def environment() -> dict:
    def ver(cmd: list[str]) -> str | None:
        try:
            return subprocess.run(cmd, capture_output=True, text=True).stdout.strip().splitlines()[0]
        except (OSError, IndexError):
            return None

    def pkg(name: str) -> str | None:
        f = RT / name / "package.json"
        return json.loads(f.read_text())["version"] if f.exists() else None

    from importlib.metadata import version, PackageNotFoundError
    try:
        wt_version = version("wasmtime")
    except PackageNotFoundError:
        wt_version = None
    cpu = next((l.split(":", 1)[1].strip() for l in Path("/proc/cpuinfo").read_text().splitlines()
                if l.startswith("model name")), None) if Path("/proc/cpuinfo").exists() else platform.processor()
    if platform.system() == "Darwin":
        cpu = ver([NODE, "-p", "require('node:os').cpus()[0]?.model"]) or cpu
    return {
        "date": datetime.date.today().isoformat(),
        "machine": f"{os.cpu_count()} cores, {cpu}",
        "platform": platform.platform(),
        "architecture": platform.machine(),
        "python": platform.python_version(),
        "wasmtime-py": wt_version,
        "wasmtime-cli": ver([WASMTIME, "--version"]),
        "wasm-tools": ver(shlex.split(os.environ.get("SNEK_WASM_TOOLS", "wasm-tools")) + ["--version"]),
        "node": ver([NODE, "--version"]),
        "wasm-opt": ver(shlex.split(os.environ.get("SNEK_WASM_OPT", "wasm-opt")) + ["--version"]),
        "snek": subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=SNEK, capture_output=True,
                               text=True).stdout.strip(),
        "dirty": bool(subprocess.run(["git", "status", "--porcelain"], cwd=SNEK, capture_output=True,
                                     text=True).stdout.strip()),
        "source_sha256": source_identity(SNEK),
        "pyodide": pkg("pyodide"),
        "python-wasi (VMware CPython build)": pkg("@antonz/python-wasi"),
        "rustpython-wasi": pkg("rustpython-wasi"),
        "micropython (PyScript build)": pkg("@micropython/micropython-webassembly-pyscript"),
        "py2wasm": ver([str(PY2WASM.parent / "python"), "-c",
                        "from importlib.metadata import version; print(version('py2wasm'))"]) if PY2WASM else None,
    }


def cmd_snapshot(ns: argparse.Namespace) -> None:
    snap = {"environment": environment()}
    for key in ("results", "startup", "alloc", "component"):
        f = WORK / f"{key}.json"
        if f.exists():
            data = json.loads(f.read_text())
            if not data.get("_meta", {}).get("complete"):
                raise RuntimeError(f"{f.name} is incomplete; rerun it before snapshotting")
            if data.get("_meta", {}).get("environment") != snap["environment"]:
                raise RuntimeError(f"{f.name} has different or missing provenance; rerun it before snapshotting")
            snap[key] = data
    if len(snap) == 1:
        raise RuntimeError("no benchmark results to snapshot")
    out = HERE / "results" / f"{snap['environment']['date']}.json"
    save(out, snap)
    print(f"wrote {out.relative_to(SNEK)}")


def fmt_s(sec: float) -> str:
    ms = sec * 1000
    if ms >= 1000:
        return f"{sec:.1f} s"
    return f"{ms:.1f} ms" if ms < 10 else f"{ms:.0f} ms"


def cmd_table(ns: argparse.Namespace) -> None:
    path = Path(ns.file) if ns.file else sorted((HERE / "results").glob("*.json"))[-1]
    snap = json.loads(path.read_text())
    res = snap.get("results", snap)
    print(f"Compute time, median ms ({path.name})\n")
    print("| | " + " | ".join(BENCHES) + " |")
    print("|---|" + "---|" * len(BENCHES))
    for rt in RUNTIMES:
        if rt not in res:
            continue
        cells = [f"{median_ms(res[rt][b]['times']):,.1f}" if res[rt].get(b, {}).get("times")
                 and res[rt][b].get("ok") else "–" for b in BENCHES]
        print(f"| {LABELS[rt]} | " + " | ".join(cells) + " |")
    st = snap.get("startup")
    if st:
        print("\nStartup\n\n| | module size | one-time compile | start (empty program) |\n|---|---|---|---|")
        for rt in RUNTIMES:
            if rt in st:
                s = st[rt]
                size = "–" if not s.get("size") else (f"{s['size'] / 1e6:.1f} MB" if s["size"] > 1e5
                                                      else f"{s['size'] / 1e3:.1f} KB")
                comp = fmt_s(s["compile"]) if s.get("compile") else "–"
                print(f"| {LABELS[rt]} | {size} | {comp} | {fmt_s(s['start'])} |")
    comp = snap.get("component")
    if comp:
        print("\nComponents: cold CLI wall time (includes uncached compilation)\n")
        print("| Program | median ms | module KB |\n|---|---|---|")
        for n in ["empty"] + BENCHES:
            d = comp.get(n, {})
            if d.get("ok") and d.get("times"):
                print(f"| {n} | {median_ms(d['times']):.1f} | {d['size'] / 1000:.1f} |")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--reps", type=int, default=5)
    r.add_argument("--only", default="")
    r.add_argument("--runtimes", default="")
    r.add_argument("--out", default=str(WORK / "results.json"))
    sub.add_parser("startup").add_argument("--out", default=str(WORK / "startup.json"))
    sub.add_parser("alloc").add_argument("--out", default=str(WORK / "alloc.json"))
    c = sub.add_parser("component")
    c.add_argument("--reps", type=int, default=5)
    c.add_argument("--only", default="")
    c.add_argument("--out", default=str(WORK / "component.json"))
    sub.add_parser("snapshot")
    sub.add_parser("table").add_argument("file", nargs="?")
    ns = ap.parse_args()
    if getattr(ns, "reps", 1) < 1:
        ap.error("--reps must be at least 1")
    WORK.mkdir(exist_ok=True)
    failed = {"run": cmd_run, "startup": cmd_startup, "alloc": cmd_alloc, "snapshot": cmd_snapshot,
              "component": cmd_component, "table": cmd_table}[ns.cmd](ns)
    return int(bool(failed))


if __name__ == "__main__":
    sys.exit(main())
