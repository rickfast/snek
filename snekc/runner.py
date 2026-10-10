"""Run a compiled module under WASI.

Prefers Wasmtime (the `wasmtime` pip package, or the `wasmtime` CLI, which has
GC on by default since v47); falls back to Node's built-in WASI.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

NODE_RUNNER = Path(__file__).with_name("node_wasi.mjs")


@dataclass
class RunResult:
    code: int
    stdout: str
    stderr: str


def run(wasm: Path, args: list[str] | None = None) -> RunResult:
    try:
        import wasmtime  # noqa: F401
    except ImportError:
        pass
    else:
        return run_wasmtime_py(wasm, args or [])
    if shutil.which("wasmtime"):
        p = subprocess.run(["wasmtime", "run", "-W", "gc=y", str(wasm), *(args or [])],
                           capture_output=True, text=True)
        return RunResult(p.returncode, p.stdout, p.stderr)
    if shutil.which("node"):
        p = subprocess.run(["node", "--no-warnings", str(NODE_RUNNER), str(wasm), *(args or [])],
                           capture_output=True, text=True)
        return RunResult(p.returncode, p.stdout, p.stderr)
    raise RuntimeError("no WebAssembly runtime found: `pip install wasmtime` or install Node 22+")


def run_wasmtime_py(wasm: Path, args: list[str]) -> RunResult:
    import wasmtime

    cfg = wasmtime.Config()
    cfg.wasm_gc = True
    cfg.wasm_function_references = True
    engine = wasmtime.Engine(cfg)
    linker = wasmtime.Linker(engine)
    linker.define_wasi()
    store = wasmtime.Store(engine)
    with tempfile.TemporaryDirectory() as d:
        out, err = Path(d, "stdout"), Path(d, "stderr")
        wc = wasmtime.WasiConfig()
        wc.argv = [wasm.name, *args]
        wc.stdout_file = str(out)
        wc.stderr_file = str(err)
        store.set_wasi(wc)
        module = wasmtime.Module.from_file(engine, str(wasm))
        code = 0
        try:
            inst = linker.instantiate(store, module)
            inst.exports(store)["_start"](store)
        except wasmtime.ExitTrap as e:
            code = e.code
        except wasmtime.Trap as e:
            code = 134
            err.write_text(err.read_text() + f"trap: {e.message}\n")
        return RunResult(code, out.read_text(), err.read_text())
