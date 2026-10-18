"""Host metadata, clocks, cancellation, random short reads and ABI allocation."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from snekc import CompileError, component, driver, runner
from check_component_lifecycle import harness


def provider(guest, feature, body, signature, declarations="", exports=""):
    """Small test providers deliberately use controlled data, not real entropy."""
    features = component.library.features(guest)
    markers = ''.join(f'(import "snek:component" "{next(iter(component.library.IMPORTS[f]))}"' for f in sorted(features))
    header = component.wrap(markers).split('(core module $memory', 1)[0]
    instance = {"random": "random", "system-clock": "system-clock", "monotonic-clock": "monotonic-clock"}[feature]
    function = "get-random-bytes" if feature == "random" else "now"
    bindings = ' '.join(f'(with "wasi:cli/{name}@0.3.0" (instance ${name}))' for name in ("types", "stdout", "stderr", "exit"))
    bindings += ' ' + ' '.join(f'(with "{name}" (instance ${"custom" if name == f"wasi:{'random' if feature == 'random' else 'clocks'}/{instance}@0.3.0" else name.split("/")[1].split("@")[0]}))'
                              for name in component.library.world_imports(features))
    return header + f'''
      (core module $provider
        (memory (export "memory") 2)
        (global $next (mut i32) (i32.const 0))
        (global $calls (mut i32) (i32.const 0))
        {body})
      (core instance $provider (instantiate $provider))
      (alias core export $provider "memory" (core memory $provider-memory))
      (alias core export $provider "get" (core func $get))
      {declarations}
      (func $provided {signature} (canon lift (core func $get) (memory $provider-memory)))
      (instance $custom (export "{function}" (func $provided)) {exports})
      {guest.replace('(component', '(component $guest', 1)}
      (instance $guest (instantiate $guest {bindings}))
      (alias export $guest "wasi:cli/run@0.3.0" (instance $guest-run))
      (export "wasi:cli/run@0.3.0" (instance $guest-run)))'''


def random_provider(guest, mode):
    n = '(i32.add (i32.wrap_i64 (local.get $requested)) (i32.const 1))' if mode == "oversize" else '(i32.const 0)' if mode == "zero" else '(i32.const 4)' if mode == "reject" else '''(if (result i32) (i64.lt_u (local.get $requested) (i64.const 3))
          (then (i32.wrap_i64 (local.get $requested))) (else (i32.const 3)))'''
    byte = '''(if (result i32) (i32.eq (global.get $calls) (i32.const 0)) (then (i32.const 255))
      (else (if (result i32) (i32.eqz (local.get $i)) (then (i32.const 1)) (else (i32.const 0)))))''' if mode == "reject" else '(global.get $next)'
    body = f'''
      (func (export "get") (param $requested i64) (result i32) (local $n i32) (local $i i32)
        (local.set $n {n})
        (block $done (loop $fill
          (br_if $done (i32.ge_u (local.get $i) (local.get $n)))
          (i32.store8 offset=4096 (local.get $i) {byte})
          (global.set $next (i32.add (global.get $next) (i32.const 1)))
          (local.set $i (i32.add (local.get $i) (i32.const 1))) (br $fill)))
        (global.set $calls (i32.add (global.get $calls) (i32.const 1)))
        (i32.store (i32.const 0) (i32.const 4096)) (i32.store (i32.const 4) (local.get $n)) (i32.const 0))'''
    return provider(guest, "random", body, '(param "max-len" u64) (result $test-bytes)',
                    '(type $test-bytes (list u8))')


class HostLibraryTests(unittest.TestCase):
    def test_node_empty_environment(self):
        source = 'from snek import os\nprint(os.getenv("ABSENT") is None)\nprint(os.getenv("ABSENT", "fallback"))\n'
        for optimize in self.modes:
            program = self.build(source, optimize=optimize)
            with patch.dict(os.environ, {"SNEK_RUNTIME": "node"}):
                result = runner.run_binary(program.wasm_path, env={})
            self.assertEqual((result.code, result.stdout, result.stderr), (0, b"True\nfallback\n", b""))

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="snek-host-library-")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.directory = Path(cls.temp.name)
        try:
            driver.find_wasm_opt()
        except driver.ToolError:
            cls.modes = (False,)
        else:
            cls.modes = (False, True)

    def build(self, source, component_=False, optimize=False):
        src, binary = self.directory / "main.py", self.directory / "main.wasm"
        src.write_text(source)
        return driver.build(src, binary, component=component_, optimize=optimize)

    def run_host(self, wat):
        path, binary = self.directory / "host.wat", self.directory / "host.wasm"
        path.write_text(wat)
        driver.wasm_tools("parse", str(path), "-o", str(binary))
        driver.wasm_tools("validate", "--features", "cm-async", str(binary))
        result = subprocess.run(["wasmtime", "run", str(binary)], capture_output=True, timeout=10)
        return result.returncode, result.stdout, result.stderr

    def test_arguments_environment_large_allocations_and_output(self):
        source = '''from snek import sys, os, secrets
sys.stdout.write("x" * 70000)
def main() -> None:
    args = sys.argv
    print(len(args), args[1], args[2] == "", len(args[3]))
    args[1] = "changed"
    print(sys.argv[1])
    print(len(os.getenv("VALUE", "missing")) + len(os.getenv("VALUE2", "missing")), os.getenv("EMPTY", "notempty") == "")
    print(os.getenv("ABSENT") is None, os.getenv("HOME") is None)
    value = os.getenv("VALUE")
    if value is not None:
        print(value.startswith("世界"))
    data = secrets.token_bytes(100000)
    print(len(data), len(os.getenv("VALUE", "missing")) + len(os.getenv("VALUE2", "missing")))
main()
'''
        args = ["🐍=hi", "", "é" * 40000]
        # Still cross multiple Wasm pages, without exceeding Linux's 128-KiB
        # per-argument limit when Wasmtime receives each --env KEY=VALUE.
        env = {"VALUE": "世界" * 15000, "VALUE2": "世界" * 15000, "EMPTY": ""}
        expected = b"x" * 70000 + "4 🐍=hi True 40000\nchanged\n60000 True\nTrue True\nTrue\n100000 60000\n".encode()
        for component_ in (False, True):
            for optimize in self.modes:
                with self.subTest(component=component_, optimize=optimize):
                    built = self.build(source, component_, optimize)
                    got = runner.run_binary(built.wasm_path, args, env=env)
                    self.assertEqual((got.code, got.stdout, got.stderr), (0, expected, b""))
                    if component_:
                        driver.wasm_tools("component", "targets", str(built.wit_path), "-w", "command", str(built.wasm_path))
                        self.assertNotIn("wasi_snapshot_preview1", built.wat)

    def test_environment_defaults_preserve_evaluation_order(self):
        source = '''from snek import os, time, run
def key() -> str:
    print("key")
    return "VALUE"
async def default() -> str:
    await time.sleep(0.0)
    print("default")
    return "fallback"
async def main() -> None:
    print(os.getenv(key(), await default()))
run(main())
'''
        for component_ in (False, True):
            got = runner.run_binary(self.build(source, component_).wasm_path, env={"VALUE": "host"})
            self.assertEqual((got.code, got.stdout, got.stderr), (0, b"key\ndefault\nhost\n", b""))

    def test_clocks_timers_and_invalid_lengths(self):
        source = '''from snek import time, run, secrets
async def main() -> None:
    print(time.time() > 0.0)
    before = time.monotonic()
    await time.sleep(0.004)
    print(time.monotonic() - before >= 0.003)
    await time.sleep(0.0)
    try:
        await time.sleep(-1.0)
    except ValueError:
        print("negative")
    try:
        await time.sleep(1e309)
    except OverflowError:
        print("overflow")
    try:
        await time.sleep(1e309 - 1e309)
    except ValueError:
        print("nan")
    try:
        secrets.randbelow(0)
    except ValueError:
        print("bound")
    print(len(secrets.token_bytes(0)), secrets.token_hex(0) == "", secrets.randbelow(1))
run(main())
'''
        for component_ in (False, True):
            for optimize in self.modes:
                got = runner.run_binary(self.build(source, component_, optimize).wasm_path)
                self.assertEqual((got.code, got.stdout, got.stderr), (0, b"True\nTrue\nnegative\noverflow\nnan\nbound\n0 True 0\n", b""))

    def test_random_short_reads_bad_progress_and_rejection(self):
        source = '''from snek import secrets
def main() -> None:
    data = secrets.token_bytes(257)
    print(len(data), data[0], data[255], data[256], secrets.token_hex(2))
main()
'''
        bad = '''from snek import secrets
try:
    secrets.token_bytes(3)
except OSError:
    print("invalid host result")
'''
        for optimize in self.modes:
            built = self.build(source, True, optimize)
            self.assertEqual(self.run_host(random_provider(built.wat, "short")), (0, b"257 0 255 0 0102\n", b""))
            built = self.build('from snek import secrets\nprint(secrets.randbelow(3))\n', True, optimize)
            self.assertEqual(self.run_host(random_provider(built.wat, "reject")), (0, b"1\n", b""))
            built = self.build(bad, True, optimize)
            for mode in ("zero", "oversize"):
                self.assertEqual(self.run_host(random_provider(built.wat, mode)), (0, b"invalid host result\n", b""))

    def test_system_clock_pre_epoch_and_invalid_nanoseconds(self):
        for nanos in (500000000, 1000000000):
            source = '''from snek import time
try:
    print(time.time() == -0.5)
except OSError:
    print("invalid clock")
'''
            body = f'''(func (export "get") (result i32)
              (i64.store (i32.const 64) (i64.const -1))
              (i32.store (i32.const 72) (i32.const {nanos})) (i32.const 64))'''
            definitions = '(type $test-instant (record (field "seconds" s64) (field "nanoseconds" u32)))'
            guest = self.build(source, True).wat
            got = self.run_host(provider(guest, "system-clock", body, '(result $test-instant)', definitions,
                                         '(export "instant" (type $test-instant))'))
            self.assertEqual(got, (0, b"True\n" if nanos == 500000000 else b"invalid clock\n", b""))

    def test_monotonic_clock_uses_unsigned_wasi_marks(self):
        source = 'from snek import time\nprint(time.monotonic() > 9000000000.0)\n'
        body = '(func (export "get") (result i64) (i64.const -1))'
        definitions = '(alias export $monotonic-clock "wait-for" (func $actual-wait))'
        guest = self.build(source, True).wat
        got = self.run_host(provider(guest, "monotonic-clock", body, '(result u64)', definitions,
                                     '(export "wait-for" (func $actual-wait))'))
        self.assertEqual(got, (0, b"True\n", b""))

    def test_timer_cancellation_cleanup_and_repeated_invocations(self):
        source = '''from snek import time, sys, run
async def main() -> None:
    try:
        await time.sleep(60.0)
    finally:
        await time.sleep(0.001)
        print(len(sys.argv))
        sys.argv.append("local")
        print("cleanup")
run(main())
'''
        for optimize in self.modes:
            started = time.monotonic()
            built = self.build(source, True, optimize)
            self.assertEqual(self.run_host(harness(built.wat, True)), (0, b"1\ncleanup\n1\ncleanup\n", b""))
            self.assertLess(time.monotonic() - started, 10.0)

    def test_aliases_signatures_and_frontend_parity(self):
        sources = ['from snek import os as config\nprint(config.getenv("", "ok"))\n',
                   'import snek.time as clock\nprint(clock.monotonic() >= 0.0)\n',
                   'from snek.sys import argv\nprint(len(argv) > 0)\n',
                   'from snek.secrets import randbelow as choose\nprint(choose(1))\n',
                   (ROOT / "examples/stdlib_host.py").read_text()]
        for source in sources:
            for component_ in (False, True):
                self.assertEqual(driver.compile_to_wat(source, component=component_, frontend="cpython"),
                                 driver.compile_to_wat(source, component=component_, frontend="snek"))
        for body in ('os.getenv()', 'os.getenv("x", 1)', 'os.getenv("x") + "suffix"',
                     'time.sleep()', 'time.sleep("one")', 'time.time(1)', 'secrets.token_bytes(1.0)',
                     'secrets.randbelow(True)', 'secrets.token_hex(n=1)', 'sys.argv()', 'sys.argv = []'):
            with self.subTest(body=body), self.assertRaises(CompileError):
                driver.compile_to_wat('from snek import os, time, sys, secrets\n' + body)

    def test_minimal_imports_and_exact_worlds(self):
        cases = [('from snek import sys\nprint(len(sys.argv))\n', {"environment"}),
                 ('from snek import time\nprint(time.time())\n', {"system-clock"}),
                 ('from snek import time, run\nrun(time.sleep(0.0))\n', {"monotonic-clock"}),
                 ('from snek import secrets\nprint(secrets.randbelow(1))\n', {"random"})]
        for source, required in cases:
            built = self.build(source, True)
            wit = built.wit_path.read_text()
            for interface in ("environment", "system-clock", "monotonic-clock", "random"):
                self.assertEqual(f'/{interface}@0.3.0;' in wit, interface in required)
            self.assertNotIn('import wasi:cli/stdin', wit)
            driver.wasm_tools("component", "targets", str(built.wit_path), "-w", "command", str(built.wasm_path))

    def test_cli_explicit_grants_and_no_ambient_environment(self):
        source = '''from snek import sys, os
print(sys.argv[1:])
print(os.getenv("GREETING", "missing"), os.getenv("OPTION", "missing"), os.getenv("HIDDEN") is None)
'''
        path = self.directory / "cli.py"
        path.write_text(source)
        env = dict(os.environ, PYTHONPATH=str(ROOT), OPTION="inherited", HIDDEN="not granted")
        for extra in ([], ["--component"]):
            cmd = [sys.executable, "-m", "snekc", "run", str(path), "--no-opt", *extra,
                   "--env", "GREETING=hello", "--env", "OPTION", "--", "--env=guest"]
            result = subprocess.run(cmd, input=b"", capture_output=True, env=env, cwd=ROOT, timeout=15)
            self.assertEqual((result.returncode, result.stdout, result.stderr),
                             (0, b"['--env=guest']\nhello inherited True\n", b""))
        binary = self.build(source).wasm_path
        for bad in ({"": "x"}, {"A=B": "x"}, {"A": "x\0"}):
            with self.assertRaises(ValueError):
                runner.run_binary(binary, env=bad)

    def test_core_metadata_clock_and_random_errors(self):
        try:
            import wasmtime
        except ImportError:
            self.skipTest("Python Wasmtime needed for core host errors")
        cases = [("os.getenv(\"x\")", "environ", None),
                 ("len(sys.argv)", "args", None),
                 ("time.time()", "clock", None),
                 ("secrets.token_bytes(2)", "random", None),
                 ("len(sys.argv)", "args", b"\xc0\xaf\0")]
        for operation, failing, raw in cases:
            source = f'from snek import os, sys, time, secrets\ntry:\n    {operation}\nexcept OSError:\n    print("caught")\n'
            built = self.build(source)
            cfg = wasmtime.Config()
            cfg.wasm_gc = cfg.wasm_function_references = cfg.wasm_exceptions = True
            engine = wasmtime.Engine(cfg)
            store = wasmtime.Store(engine)
            module = wasmtime.Module.from_file(engine, str(built.wasm_path))
            linker = wasmtime.Linker(engine)
            output = bytearray()

            def sizes(caller, count, length):
                if raw is None:
                    return 29
                memory = caller.get("memory")
                memory.write(caller, struct.pack("<I", 1), count)
                memory.write(caller, struct.pack("<I", len(raw)), length)
                return 0

            def get(caller, pointers, buffer):
                memory = caller.get("memory")
                memory.write(caller, struct.pack("<I", buffer), pointers)
                memory.write(caller, raw, buffer)
                return 0

            def write(caller, fd, iov, count, result):
                memory = caller.get("memory")
                ptr, n = struct.unpack("<II", memory.read(caller, iov, iov + 8))
                output.extend(memory.read(caller, ptr, ptr + n))
                memory.write(caller, struct.pack("<I", n), result)
                return 0

            i32, i64 = wasmtime.ValType.i32(), wasmtime.ValType.i64()
            callbacks = {"fd_write": (write, [i32] * 4, True), "proc_exit": (lambda code: self.fail(f"exit {code}"), [i32], False)}
            if failing in ("args", "environ"):
                callbacks[failing + "_sizes_get"] = (sizes, [i32] * 2, True)
                callbacks[failing + "_get"] = (get, [i32] * 2, True)
            elif failing == "clock":
                callbacks["clock_time_get"] = (lambda a, b, c: 29, [i32, i64, i32], False)
            else:
                callbacks["random_get"] = (lambda a, b: 29, [i32, i32], False)
            for name, (callback, params, access) in callbacks.items():
                linker.define_func("wasi_snapshot_preview1", name, wasmtime.FuncType(params, [] if name == "proc_exit" else [i32]),
                                   callback, access_caller=access)
            instance = linker.instantiate(store, module)
            instance.exports(store)["_start"](store)
            self.assertEqual(bytes(output), b"caught\n")

    def test_node_environment_transport(self):
        node = os.environ.get("SNEK_NODE", "node")
        if not shutil.which(node):
            self.skipTest("Node unavailable")
        try:
            runner.check_node(node)
        except RuntimeError as error:
            self.skipTest(str(error))
        source = 'from snek import os, sys\nprint(sys.argv[1:], os.getenv("NAME", "missing"), os.getenv("HOME") is None)\n'
        binary = self.build(source).wasm_path
        with patch.dict(os.environ, SNEK_RUNTIME="node"):
            got = runner.run_binary(binary, ["世界"], env={"NAME": "🐍"})
        self.assertEqual((got.code, got.stdout, got.stderr), (0, "['世界'] 🐍 True\n".encode(), b""))


if __name__ == "__main__":
    unittest.main()
