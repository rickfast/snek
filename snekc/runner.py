"""Run a compiled module under WASI.

Runtimes, in order of preference (or force one with SNEK_RUNTIME):
  wasmtime-py  the `wasmtime` pip package (Wasmtime 47+ API)
  wasmtime     the `wasmtime` CLI; GC and exception handling are on by default since v47
  node         Node 24+ via its built-in WASI. Node 22 (V8 12.4) mis-executes a
               `try_table` that produces a value, which wasm-opt -O2/-O3 generates
               when it turns a caught `throw` into a branch, so it is refused.
               SNEK_NODE can point at a specific node binary.
"""

from __future__ import annotations

import os
import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

NODE_RUNNER = Path(__file__).with_name("node_wasi.mjs")
MIN_NODE = 24


@dataclass
class RunResult:
    code: int
    stdout: str
    stderr: str


@dataclass
class BinaryRunResult:
    code: int
    stdout: bytes
    stderr: bytes

    def text(self) -> RunResult:
        return RunResult(self.code, self.stdout.decode("utf-8", errors="replace"),
                         self.stderr.decode("utf-8", errors="replace"))


def available_runtime() -> str:
    forced = os.environ.get("SNEK_RUNTIME")
    if forced:
        if forced not in ("wasmtime-py", "wasmtime", "node"):
            raise RuntimeError(f"SNEK_RUNTIME must be wasmtime-py, wasmtime or node, not {forced!r}")
        return forced
    try:
        import wasmtime  # noqa: F401
    except ImportError:
        pass
    else:
        return "wasmtime-py"
    if shutil.which("wasmtime"):
        return "wasmtime"
    if shutil.which(os.environ.get("SNEK_NODE", "node")):
        return "node"
    raise RuntimeError(f"no WebAssembly runtime found: `pip install wasmtime`, install the wasmtime CLI (47+), "
                       f"or install Node {MIN_NODE}+")


def run(wasm: Path, args: list[str] | None = None, *, env: dict[str, str] | None = None) -> RunResult:
    return run_binary(wasm, args, env=env).text()


def run_binary(wasm: Path, args: list[str] | None = None, *, stdin: bytes | None = b"",
               env: dict[str, str] | None = None) -> BinaryRunResult:
    """Capture exact output bytes. stdin=None inherits the caller's stdin."""
    args = args or []
    granted = dict(env or {})
    for name, value in granted.items():
        if not isinstance(name, str) or not isinstance(value, str) or not name or "=" in name or "\0" in name or "\0" in value:
            raise ValueError("environment grants require nonempty names without '=' and strings without NUL")
    env_args = [arg for name, value in granted.items() for arg in ("--env", name + "=" + value)]
    with wasm.open("rb") as f:
        component = f.read(8) == b"\x00asm\x0d\x00\x01\x00"
    if component:
        forced = os.environ.get("SNEK_RUNTIME")
        if forced and forced != "wasmtime":
            raise RuntimeError("components require the Wasmtime CLI (49+); set SNEK_RUNTIME=wasmtime")
        if not shutil.which("wasmtime"):
            raise RuntimeError("components require the Wasmtime CLI (49+)")
        p = subprocess.run(["wasmtime", "run", *env_args, str(wasm), *args], input=stdin, capture_output=True)
        return BinaryRunResult(p.returncode, p.stdout, p.stderr)
    runtime = available_runtime()
    if runtime == "wasmtime-py":
        return run_wasmtime_py_binary(wasm, args, stdin=stdin, env=granted)
    if runtime == "wasmtime":
        p = subprocess.run(["wasmtime", "run", *env_args, str(wasm), *args], input=stdin, capture_output=True)
        return BinaryRunResult(p.returncode, p.stdout, p.stderr)
    node = os.environ.get("SNEK_NODE", "node")
    check_node(node)
    host_env = dict(os.environ, SNEK_WASI_ENV=json.dumps(granted))
    p = subprocess.run([node, "--no-warnings", str(NODE_RUNNER), str(wasm), *args],
                       input=stdin, capture_output=True, env=host_env)
    return BinaryRunResult(p.returncode, p.stdout, p.stderr)


def check_node(node: str) -> None:
    version = subprocess.run([node, "--version"], capture_output=True, text=True).stdout.strip()
    try:
        major = int(version.lstrip("v").split(".")[0])
    except ValueError:
        return
    if major < MIN_NODE:
        raise RuntimeError(f"Node {version} is too old: its V8 mis-executes Wasm `try_table` blocks that "
                           f"produce a value (wasm-opt generates these). Use Node {MIN_NODE}+ or Wasmtime.")


def run_wasmtime_py(wasm: Path, args: list[str], *, env: dict[str, str] | None = None) -> RunResult:
    return run_wasmtime_py_binary(wasm, args, env=env).text()


def run_wasmtime_py_binary(wasm: Path, args: list[str], *, stdin: bytes | None = b"",
                           env: dict[str, str] | None = None) -> BinaryRunResult:
    import wasmtime

    cfg = wasmtime.Config()
    cfg.wasm_gc = True
    cfg.wasm_function_references = True
    cfg.wasm_exceptions = True
    engine = wasmtime.Engine(cfg)
    linker = wasmtime.Linker(engine)
    linker.define_wasi()
    store = wasmtime.Store(engine)
    with tempfile.TemporaryDirectory() as d:
        out, err = Path(d, "stdout"), Path(d, "stderr")
        wc = wasmtime.WasiConfig()
        wc.argv = [wasm.name, *args]
        wc.env = list((env or {}).items())
        wc.stdout_file = str(out)
        wc.stderr_file = str(err)
        if stdin is None:
            wc.inherit_stdin()
        else:
            input_file = Path(d, "stdin")
            input_file.write_bytes(stdin)
            wc.stdin_file = str(input_file)
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
            err.write_bytes(err.read_bytes() + f"trap: {e.message}\n".encode())
        return BinaryRunResult(code, out.read_bytes(), err.read_bytes())
