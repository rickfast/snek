"""Byte-stream ABI, CLI transport and failure handling; core WASI only."""
from __future__ import annotations

from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from snekc import CompileError, driver, runner

COPY = "from snek import read_bytes, write_bytes\nwrite_bytes(read_bytes())\n"


class ByteIOTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix="snek-byte-io-")
        cls.addClassCleanup(cls.directory.cleanup)
        cls.source = Path(cls.directory.name, "copy.py")
        cls.source.write_text(COPY)
        cls.binary = Path(cls.directory.name, "copy.wasm")
        driver.build(cls.source, cls.binary, optimize=False)

    def test_binary_round_trip_and_eof(self):
        for data in (b"", b"\xff", bytes(range(256)) * 257):
            result = runner.run_binary(self.binary, stdin=data)
            self.assertEqual((result.code, result.stdout, result.stderr), (0, data, b""))

    def test_cli_preserves_stdin_and_non_utf8_stdout(self):
        data = bytes(range(256)) * 300
        result = subprocess.run([sys.executable, "-m", "snekc", "run", str(self.source), "--no-opt"],
                                input=data, capture_output=True, cwd=ROOT)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, data, b""))

    def test_text_output_order_and_invalid_bytes_are_atomic(self):
        source = Path(self.directory.name, "order.py")
        binary = Path(self.directory.name, "order.wasm")
        source.write_text('''from snek import read_bytes, write_bytes
print("before")
write_bytes(read_bytes())
print("after")
try:
    write_bytes([88, 256])
except ValueError as error:
    print(str(error))
try:
    write_bytes([-1])
except ValueError:
    print("negative")
write_bytes([])
''')
        driver.build(source, binary, optimize=False)
        result = runner.run_binary(binary, stdin=b"\xff\x00")
        self.assertEqual(result.code, 0, result.stderr)
        self.assertEqual(result.stdout, b"before\n\xff\x00after\nbyte values must be in range 0..255\nnegative\n")

    def test_checked_signature_and_target(self):
        invalid = ["read_bytes(1)", "write_bytes()", "write_bytes([1.0])", "write_bytes(data=[1])", "write_bytes([True])"]
        for body in invalid:
            with self.subTest(body=body), self.assertRaises(CompileError):
                driver.compile_to_wat("from snek import read_bytes, write_bytes\n" + body)
        with self.assertRaisesRegex(CompileError, "core WASI target"):
            driver.compile_to_wat(COPY, component=True)

    def test_imported_names_cannot_be_redefined(self):
        for source in ("from snek import read_bytes\ndef read_bytes() -> None:\n    pass\n",
                       "def read_bytes() -> None:\n    pass\nfrom snek import read_bytes\n"):
            with self.assertRaises(CompileError):
                driver.compile_to_wat(source)

    def test_partial_host_io_and_errors(self):
        try:
            import wasmtime
        except ImportError:
            self.skipTest("Python Wasmtime needed for custom short-I/O host")
        cfg = wasmtime.Config()
        cfg.wasm_gc = cfg.wasm_function_references = cfg.wasm_exceptions = True
        engine = wasmtime.Engine(cfg)
        module = wasmtime.Module.from_file(engine, str(self.binary))
        for fault in ("", "read", "oversize_read", "write", "zero", "oversize"):
            state = {"pos": 0, "code": 0, "stdout": bytearray(), "stderr": bytearray()}
            data = bytes(range(256))

            def fd_read(caller, fd, iov, count, result):
                if fault == "read":
                    return 29
                memory = caller.get("memory")
                ptr, length = struct.unpack("<II", memory.read(caller, iov, iov + 8))
                if fault == "oversize_read":
                    memory.write(caller, struct.pack("<I", length + 1), result)
                    return 0
                chunk = data[state["pos"]:state["pos"] + min(length, 3)]
                state["pos"] += len(chunk)
                memory.write(caller, chunk, ptr)
                memory.write(caller, struct.pack("<I", len(chunk)), result)
                return 0

            def fd_write(caller, fd, iov, count, result):
                memory = caller.get("memory")
                ptr, length = struct.unpack("<II", memory.read(caller, iov, iov + 8))
                if fd == 1 and fault == "write":
                    return 29
                size = min(2, length)
                if fd == 1 and fault in ("zero", "oversize"):
                    size = 0 if fault == "zero" else length + 1
                else:
                    state["stdout" if fd == 1 else "stderr"].extend(memory.read(caller, ptr, ptr + size))
                memory.write(caller, struct.pack("<I", size), result)
                return 0

            def exit_(code):
                state["code"] = code

            store = wasmtime.Store(engine)
            linker = wasmtime.Linker(engine)
            i32 = wasmtime.ValType.i32()
            for name, callback in (("fd_read", fd_read), ("fd_write", fd_write)):
                linker.define_func("wasi_snapshot_preview1", name, wasmtime.FuncType([i32] * 4, [i32]), callback, access_caller=True)
            linker.define_func("wasi_snapshot_preview1", "proc_exit", wasmtime.FuncType([i32], []), exit_)
            instance = linker.instantiate(store, module)
            try:
                instance.exports(store)["_start"](store)
            except wasmtime.Trap:
                # proc_exit's mock returns, then rt.panic reaches unreachable.
                self.assertEqual(state["code"], 1)
            with self.subTest(fault=fault):
                self.assertEqual(state["code"], 1 if fault else 0)
                self.assertEqual(bytes(state["stdout"]), b"" if fault else data)
                if fault:
                    self.assertIn(b"RuntimeError:", state["stderr"])


if __name__ == "__main__":
    unittest.main()
