"""CLI library ABI, real host errors, cancellation, and frontend parity."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import struct
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from snekc import CompileError, component, driver, runner
from check_component_lifecycle import harness


PIPE = (ROOT / "examples/stdlib_pipe.py").read_text()


def failing_host(guest: str, error: int | None, *, stdin: bool = False) -> str:
    """An actual component provider that returns the requested WASI error."""
    header = component.wrap("").split('(core module $memory', 1)[0]
    operation = ('(result i32) (local $stream i64)' if stdin else '(param $reader i32) (result i32)')
    start = '''(local.set $stream (call $stream-new))
        (call $writer-drop (i32.wrap_i64 (i64.shr_u (local.get $stream) (i64.const 32))))''' if stdin else '(call $reader-drop (local.get $reader))'
    result = '''(i32.store (i32.const 8) (i32.wrap_i64 (local.get $stream)))
        (i32.store (i32.const 12) (i32.wrap_i64 (local.get $future))) (i32.const 8)''' if stdin else '(i32.wrap_i64 (local.get $future))'
    lifted = ('(func (result (tuple $bytes $future)) (canon lift (core func $fail) (memory $stub-memory)))' if stdin else
              '(func (param "data" $bytes) (result $future) (canon lift (core func $fail)))')
    export = "read-via-stream" if stdin else "write-via-stream"
    # A future.write may retain this immutable scratch buffer until the guest
    # reads the future. The provider is called once per fresh test store.
    write = f'''(i32.store8 (i32.const 0) (i32.const 1))
          (i32.store8 (i32.const 1) (i32.const {error}))
          (drop (call $write (local.get $writer) (i32.const 0)))''' if error is not None else ""
    return header + f'''
      (core module $mem (memory (export "memory") 1))
      (core instance $mem (instantiate $mem))
      (alias core export $mem "memory" (core memory $stub-memory))
      (core func $future-new (canon future.new $future))
      (core func $future-write (canon future.write $future (memory $stub-memory) async))
      (core func $future-drop (canon future.drop-writable $future))
      (core func $reader-drop (canon stream.drop-readable $bytes))
      (core func $stream-new (canon stream.new $bytes))
      (core func $writer-drop (canon stream.drop-writable $bytes))
      (core instance $api (export "new" (func $future-new)) (export "write" (func $future-write))
        (export "drop" (func $future-drop)) (export "reader-drop" (func $reader-drop))
        (export "stream-new" (func $stream-new)) (export "writer-drop" (func $writer-drop))
        (export "memory" (memory $stub-memory)))
      (core module $stub
        (import "api" "memory" (memory 1))
        (import "api" "new" (func $new (result i64)))
        (import "api" "write" (func $write (param i32 i32) (result i32)))
        (import "api" "drop" (func $drop (param i32)))
        (import "api" "reader-drop" (func $reader-drop (param i32)))
        (import "api" "stream-new" (func $stream-new (result i64)))
        (import "api" "writer-drop" (func $writer-drop (param i32)))
        (func (export "fail") {operation} (local $future i64) (local $writer i32)
          {start}
          (local.set $future (call $new))
          (local.set $writer (i32.wrap_i64 (i64.shr_u (local.get $future) (i64.const 32))))
          {write}
          {result}))
      (core instance $stub (instantiate $stub (with "api" (instance $api))))
      (alias core export $stub "fail" (core func $fail))
      (func $fail {lifted[6:-1]})
      (instance $failed (export "error-code" (type $error)) (export "{export}" (func $fail)))
      {guest.replace('(component', '(component $guest', 1)}
      (instance $guest (instantiate $guest
        (with "wasi:cli/types@0.3.0" (instance $types))
        (with "wasi:cli/stdout@0.3.0" (instance {'$stdout' if stdin else '$failed'}))
        (with "wasi:cli/stderr@0.3.0" (instance $stderr))
        (with "wasi:cli/exit@0.3.0" (instance $exit))
        {('(with "wasi:cli/stdin@0.3.0" (instance $failed))' if stdin else '')}))
      (alias export $guest "wasi:cli/run@0.3.0" (instance $guest-run))
      (export "wasi:cli/run@0.3.0" (instance $guest-run)))'''


class StdlibTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="snek-stdlib-")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.directory = Path(cls.temp.name)
        try:
            driver.find_wasm_opt()
        except driver.ToolError:
            cls.modes = (False,)
        else:
            cls.modes = (False, True)

    def build(self, source, *, component=False, optimize=False):
        src, wasm = self.directory / "program.py", self.directory / "program.wasm"
        src.write_text(source)
        return driver.build(src, wasm, component=component, optimize=optimize)

    def run_host(self, wat):
        path, binary = self.directory / "host.wat", self.directory / "host.wasm"
        path.write_text(wat)
        driver.wasm_tools("parse", str(path), "-o", str(binary))
        driver.wasm_tools("validate", "--features", "cm-async", str(binary))
        return runner.run_binary(binary)

    def test_binary_roundtrip_chunk_boundaries_and_exact_wit(self):
        for component_ in (False, True):
            for optimize in self.modes:
                result = self.build(PIPE, component=component_, optimize=optimize)
                if component_:
                    self.assertIn('import wasi:cli/stdin@0.3.0;', result.wit_path.read_text())
                    driver.wasm_tools("component", "targets", str(result.wit_path), "-w", "command", str(result.wasm_path))
                    self.assertNotIn("wasi_snapshot_preview1", result.wat)
                for data in (b"", b"\xff\x00", bytes(range(256)) * 1025):
                    with self.subTest(component=component_, optimize=optimize, size=len(data)):
                        got = runner.run_binary(result.wasm_path, stdin=data)
                        self.assertEqual((got.code, got.stdout, got.stderr), (0, data, b""))

    def test_text_binary_counts_order_and_atomic_validation(self):
        source = '''from snek import sys, run
async def main() -> None:
    print("before")
    count = sys.stdout.write("世界🐍" * 24000)
    sys.stderr.write("error 世界\\n")
    values = [0, 255, 10]
    n = sys.stdout.buffer.write(values)
    values[0] = 88
    try:
        sys.stdout.buffer.write([88, 256])
    except ValueError:
        print("invalid")
    try:
        sys.stderr.buffer.write([-1])
    except ValueError:
        print("negative")
    print(count, n)
    await sys.stdout.drain()
    await sys.stderr.buffer.drain()
run(main())
'''
        src = self.directory / "oracle.py"
        src.write_text(source)
        expected = subprocess.run([sys.executable, str(src)], capture_output=True, env=dict(os.environ, PYTHONPATH=str(ROOT)))
        self.assertEqual(expected.returncode, 0, expected.stderr)
        for component_ in (False, True):
            for optimize in self.modes:
                with self.subTest(component=component_, optimize=optimize):
                    result = self.build(source, component=component_, optimize=optimize)
                    got = runner.run_binary(result.wasm_path)
                    self.assertEqual((got.code, got.stdout, got.stderr), (0, expected.stdout, expected.stderr))
                    if component_:
                        self.assertNotIn('import wasi:cli/stdin', result.wit_path.read_text())

    def test_incremental_reads_lines_and_retained_lookahead(self):
        source = '''from snek import run, sys, io, i32
async def consume(inp: io.BinaryInput, out: io.BinaryOutput) -> None:
    print(await inp.read(0), await inp.readline(0))
    for size in [1, 2, 64511, 64512, 64513]:
        data = await inp.read(size)
        out.write(data)
        await out.drain()
    out.write(await inp.readline(3))
    out.write(await inp.readline())
    out.write(await inp.read(7))
    out.write(await inp.readline(-2))
    out.write(await inp.read())
    print(await inp.read(4), await inp.readline(), await inp.read(-2))
async def main() -> None:
    inp = sys.stdin.buffer
    out = sys.stdout.buffer
    await consume(inp, out)
run(main())
'''
        oracle = self.directory / "oracle.py"
        oracle.write_text(source)
        cases = (b"", b"\x00\xff\nA\nB\nC", bytes(range(256)) * 1025,
                 b"x" * 193539 + b"ab\nfirst\nsecond\n" + bytes(range(256)) * 300)
        for component_ in (False, True):
            for optimize in self.modes:
                result = self.build(source, component=component_, optimize=optimize)
                for data in cases:
                    with self.subTest(component=component_, optimize=optimize, size=len(data)):
                        expected = subprocess.run([sys.executable, str(oracle)], input=data, capture_output=True,
                                                  env=dict(os.environ, PYTHONPATH=str(ROOT)))
                        got = runner.run_binary(result.wasm_path, stdin=data)
                        self.assertEqual((got.code, got.stdout, got.stderr),
                                         (expected.returncode, expected.stdout, expected.stderr))

    def test_first_class_stream_types_aliases_fields_and_cleanup(self):
        source = '''from snek import sys, io, run, i32
from snek.io import TextOutput as Output, BinaryInput as Input
class Sink:
    out: Output
    def __init__(self, out: Output) -> None:
        self.out = out
    def write(self, text: str) -> None:
        self.out.write(text)
def choose(out: io.TextOutput | None) -> io.TextOutput:
    if out is None:
        return sys.stdout
    return out
async def first(inp: Input) -> i32:
    with inp as resource:
        data = await resource.read(3)
        return len(data)
async def main() -> None:
    alias = choose(None)
    sinks = [sys.stdout, alias]
    sink = Sink(sinks[1])
    sink.write("hello\\n")
    print(alias is sys.stdout, alias.buffer is sys.stdout.buffer)
    print(alias == sys.stdout, alias != sys.stderr, alias == alias.buffer)
    print(alias in sinks, sinks.count(alias))
    print(await first(sys.stdin.buffer))
    print(sys.stdin.closed, sys.stdin.buffer.closed)
    try:
        await sys.stdin.buffer.read(0)
    except ValueError:
        print("closed input")
    try:
        with sys.stdin.buffer:
            print("never entered")
    except ValueError:
        print("closed enter")
    try:
        with alias as out:
            out.write("body\\n")
            await out.drain()
            raise RuntimeError("body failed")
    except RuntimeError:
        print("body error")
    print(alias.closed, alias.buffer.closed, sink.out.closed)
    alias.close()
    for op in [0, 1, 2, 3]:
        try:
            if op == 0:
                sink.write("bad")
            elif op == 1:
                alias.flush()
            elif op == 2:
                await alias.drain()
            else:
                alias.buffer.write([0])
        except ValueError:
            print("closed output", op)
run(main())
'''
        oracle = self.directory / "oracle.py"
        oracle.write_text(source)
        expected = subprocess.run([sys.executable, str(oracle)], input=b"abcdef", capture_output=True,
                                  env=dict(os.environ, PYTHONPATH=str(ROOT)))
        self.assertEqual(expected.returncode, 0, expected.stderr)
        for component_ in (False, True):
            self.assertEqual(driver.compile_to_wat(source, component=component_, frontend="cpython"),
                             driver.compile_to_wat(source, component=component_, frontend="snek"))
            for optimize in self.modes:
                with self.subTest(component=component_, optimize=optimize):
                    got = runner.run_binary(self.build(source, component=component_, optimize=optimize).wasm_path,
                                            stdin=b"abcdef")
                    self.assertEqual((got.code, got.stdout, got.stderr), (0, expected.stdout, expected.stderr))

    def test_bounded_component_read_finishes_before_eof(self):
        sources = ['''from snek import sys, run
async def main() -> None:
    with sys.stdin.buffer as inp:
        data = await inp.read(3)
        sys.stdout.buffer.write(data)
        await sys.stdout.drain()
run(main())
''', '''from snek import sys, run
async def main() -> None:
    data = await sys.stdin.buffer.read(3)
    sys.stdout.buffer.write(data)
    await sys.stdout.drain()
run(main())
''', '''from snek import sys, run
async def main() -> None:
    inp = sys.stdin.buffer
    line = await inp.readline()
    sys.stdout.buffer.write(line)
    await sys.stdout.drain()
    inp.close()
run(main())
''']
        for optimize in self.modes:
            for i, source in enumerate(sources):
                with self.subTest(optimize=optimize, source=i):
                    result = self.build(source, component=True, optimize=optimize)
                    with subprocess.Popen(["wasmtime", "run", str(result.wasm_path)], stdin=subprocess.PIPE,
                                          stdout=subprocess.PIPE, stderr=subprocess.PIPE) as process:
                        data = b"abc\n" if i == 2 else b"abc"
                        process.stdin.write(data)
                        process.stdin.flush()
                        try:
                            code = process.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait()
                            self.fail("incremental stdin read waited for EOF")
                        self.assertEqual((code, process.stdout.read(), process.stderr.read()), (0, data, b""))

    def test_stream_close_reset_across_component_invocations(self):
        source = '''from snek import sys, run
async def main() -> None:
    print(sys.stdin.closed, sys.stdout.closed)
    with sys.stdin.buffer as inp:
        print(await inp.read(0))
    with sys.stdout as out:
        out.write("closed later\\n")
        await out.drain()
    print(sys.stdin.closed, sys.stdout.closed)
run(main())
'''
        for optimize in self.modes:
            result = self.build(source, component=True, optimize=optimize)
            got = self.run_host(harness(result.wat, False))
            self.assertEqual((got.code, got.stdout, got.stderr),
                             (0, b"False False\n[]\nclosed later\nTrue True\n" * 2, b""))

    def test_stream_capture_loop_cleanup_and_close_before_await(self):
        source = '''from snek import sys, io, run
from typing import Callable
async def main() -> None:
    stream = sys.stderr
    def current() -> io.TextOutput:
        return stream
    handle: Callable[[], io.TextOutput] = current
    for out in [sys.stdout, current()]:
        out.write("view\\n")
        await out.drain()
    try:
        for n in [0, 1]:
            with handle() as out:
                if n == 0:
                    continue
    except ValueError:
        print("closed on continue")
    pending = sys.stdin.buffer.read(0)
    sys.stdin.close()
    try:
        await pending
    except ValueError:
        print("closed before await")
run(main())
'''
        for component_ in (False, True):
            for optimize in self.modes:
                with self.subTest(component=component_, optimize=optimize):
                    got = runner.run_binary(self.build(source, component=component_, optimize=optimize).wasm_path)
                    self.assertEqual((got.code, got.stdout, got.stderr),
                                     (0, b"view\nclosed on continue\nclosed before await\n", b"view\n"))

    def test_stream_typing_rejects_incompatible_views_and_read_sizes(self):
        sources = [
            'def f(out: io.BinaryOutput) -> None:\n    pass\nf(sys.stdout)',
            'def f(inp: io.BinaryInput) -> None:\n    pass\nf(sys.stdout.buffer)',
            'sys.stdin.buffer.read(1.5)', 'sys.stdin.buffer.readline(size=1)',
            'sys.stdin.buffer.read(True)', 'sys.stdin.buffer.readline(1, 2)',
            'sys.stdout.buffer.buffer', 'sys.stdin.buffer.write([1])',
            'sys.stdout.read()', 'io.BinaryInput()', 'sys.stdout.closed = True',
        ]
        for source in sources:
            with self.subTest(source=source), self.assertRaises(CompileError):
                driver.compile_to_wat('from snek import sys, io\n' + source + '\n')

    def test_alias_imports_frontends_and_lexical_shadowing(self):
        forms = [("from snek import sys as cli", "cli.stdout"),
                 ("import snek.sys as cli", "cli.stdout"),
                 ("import snek.sys", "snek.sys.stdout"),
                 ("from snek.sys import stdout as out", "out")]
        for imports, stream in forms:
            source = f'{imports}\n{stream}.write("ok\\n")\n{stream}.flush()\n'
            for component_ in (False, True):
                with self.subTest(imports=imports, component=component_):
                    self.assertEqual(driver.compile_to_wat(source, component=component_, frontend="cpython"),
                                     driver.compile_to_wat(source, component=component_, frontend="snek"))
                    got = runner.run_binary(self.build(source, component=component_).wasm_path)
                    self.assertEqual((got.code, got.stdout, got.stderr), (0, b"ok\n", b""))
        # A local must not accidentally resolve to a library namespace.
        with self.assertRaises(CompileError):
            driver.compile_to_wat('from snek import sys\ndef f(sys: str) -> None:\n    sys.stdout.write("bad")\nf("x")\n')

    def test_signatures_and_reserved_imports(self):
        bad = ['sys.stdout.write(1)', 'sys.stdout.write(text="x")', 'sys.stdout.buffer.write([True])',
               'sys.stdout.buffer.write([1.0])', 'sys.stdout.flush(1)', 'sys.stdin.buffer.read(1, 2)',
               'sys.stdout.close(1)', 'sys.stdin.read()', 'run(sys.stdout.write("x"))']
        for call in bad:
            with self.subTest(call=call), self.assertRaises(CompileError):
                driver.compile_to_wat('from snek import sys, run\n' + call)
        for source in ('from snek import sys\ndef sys() -> None:\n    pass\n',
                       'def sys() -> None:\n    pass\nfrom snek import sys\n',
                       'from .snek import sys\n', 'from snek.sys import missing\n',
                       'from snek import sys as i32\n'):
            with self.subTest(source=source), self.assertRaises(CompileError):
                driver.compile_to_wat(source)

    def test_component_host_errors_are_catchable(self):
        for stdin_ in (False, True):
            action = 'await sys.stdin.buffer.read()' if stdin_ else 'sys.stdout.write("x")\n        await sys.stdout.drain()'
            source = f'''from snek import sys, run
async def main() -> None:
    try:
        {action}
    except BrokenPipeError:
        sys.stderr.write("pipe\\n")
    except OSError:
        sys.stderr.write("io\\n")
    await sys.stderr.drain()
run(main())
'''
            for optimize in self.modes:
                result = self.build(source, component=True, optimize=optimize)
                for error in (0, 1, 2):
                    with self.subTest(stdin=stdin_, optimize=optimize, error=error):
                        got = self.run_host(failing_host(result.wat, error, stdin=stdin_))
                        self.assertEqual((got.code, got.stdout, got.stderr),
                                         (0, b"", b"pipe\n" if error == 2 else b"io\n"))

    def test_repeated_invocations_and_output_cancellation(self):
        source = '''from snek import sys, run
async def main() -> None:
    try:
        sys.stdout.write("start\\n")
        await sys.stdout.drain()
        sys.stdout.write("end\\n")
    finally:
        sys.stdout.write("cleanup\\n")
        await sys.stdout.drain()
run(main())
'''
        for optimize in self.modes:
            result = self.build(source, component=True, optimize=optimize)
            for cancel in (False, True):
                with self.subTest(optimize=optimize, cancel=cancel):
                    got = self.run_host(harness(result.wat, cancel))
                    expected = (b"start\ncleanup\n" if cancel else b"start\nend\ncleanup\n") * 2
                    self.assertEqual((got.code, got.stdout, got.stderr), (0, expected, b""))

    def test_input_cancellation_releases_borrow_before_cleanup(self):
        source = '''from snek import sys, run
async def main() -> None:
    try:
        with sys.stdin.buffer as inp:
            await inp.read()
    finally:
        if not sys.stdin.closed:
            raise RuntimeError("cancellation did not close the stream")
        sys.stdout.write("cleanup\\n")
        await sys.stdout.drain()
run(main())
'''
        for optimize in self.modes:
            result = self.build(source, component=True, optimize=optimize)
            for waiting_future in (False, True):
                with self.subTest(optimize=optimize, waiting_future=waiting_future):
                    guest = failing_host(result.wat, None, stdin=True) if waiting_future else result.wat
                    path, binary = self.directory / "cancel.wat", self.directory / "cancel.wasm"
                    path.write_text(harness(guest, True))
                    driver.wasm_tools("parse", str(path), "-o", str(binary))
                    driver.wasm_tools("validate", "--features", "cm-async", str(binary))
                    # Keep the producer open without sending bytes: EOF must
                    # not complete the read before canonical subtask.cancel.
                    with subprocess.Popen(["wasmtime", "run", str(binary)], stdin=subprocess.PIPE,
                                          stdout=subprocess.PIPE, stderr=subprocess.PIPE) as process:
                        try:
                            code = process.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait()
                            self.fail("stdin cancellation hung while the producer stayed open")
                        self.assertEqual((code, process.stdout.read(), process.stderr.read()),
                                         (0, b"cleanup\n" * 2, b""))

    def test_core_partial_io_and_oserror(self):
        try:
            import wasmtime
        except ImportError:
            self.skipTest("Python Wasmtime needed for partial-I/O provider")
        source = '''from snek import sys, run
async def main() -> None:
    try:
        data = await sys.stdin.buffer.read()
        sys.stdout.buffer.write(data)
        await sys.stdout.drain()
    except OSError:
        sys.stderr.write("io\\n")
        await sys.stderr.drain()
run(main())
'''
        binary = self.build(source).wasm_path
        cfg = wasmtime.Config()
        cfg.wasm_gc = cfg.wasm_function_references = cfg.wasm_exceptions = True
        engine = wasmtime.Engine(cfg)
        module = wasmtime.Module.from_file(engine, str(binary))
        data = bytes(range(256))
        for fault in ("", "read", "oversize_read", "write", "zero", "oversize"):
            state = {"pos": 0, "stdout": bytearray(), "stderr": bytearray()}

            def fd_read(caller, fd, iov, count, result):
                if fault == "read":
                    return 29
                memory = caller.get("memory")
                ptr, length = struct.unpack("<II", memory.read(caller, iov, iov + 8))
                chunk = data[state["pos"]:state["pos"] + min(length, 3)]
                state["pos"] += len(chunk)
                memory.write(caller, chunk, ptr)
                memory.write(caller, struct.pack("<I", length + 1 if fault == "oversize_read" else len(chunk)), result)
                return 0

            def fd_write(caller, fd, iov, count, result):
                if fd == 1 and fault == "write":
                    return 29
                memory = caller.get("memory")
                ptr, length = struct.unpack("<II", memory.read(caller, iov, iov + 8))
                n = min(length, 2)
                if fd == 1 and fault in ("zero", "oversize"):
                    n = 0 if fault == "zero" else length + 1
                else:
                    state["stdout" if fd == 1 else "stderr"].extend(memory.read(caller, ptr, ptr + n))
                memory.write(caller, struct.pack("<I", n), result)
                return 0

            store = wasmtime.Store(engine)
            linker = wasmtime.Linker(engine)
            i32 = wasmtime.ValType.i32()
            for name, callback in (("fd_read", fd_read), ("fd_write", fd_write)):
                linker.define_func("wasi_snapshot_preview1", name, wasmtime.FuncType([i32] * 4, [i32]), callback, access_caller=True)
            linker.define_func("wasi_snapshot_preview1", "proc_exit", wasmtime.FuncType([i32], []),
                               lambda code: self.fail(f"unexpected exit {code}"))
            with self.subTest(fault=fault):
                instance = linker.instantiate(store, module)
                instance.exports(store)["_start"](store)
                self.assertEqual(bytes(state["stdout"]), b"" if fault else data)
                self.assertEqual(bytes(state["stderr"]), b"io\n" if fault else b"")


if __name__ == "__main__":
    unittest.main()
