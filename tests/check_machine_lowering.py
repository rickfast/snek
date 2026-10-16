"""Snek suspension CFG parity and executable integration, with no host fallback."""
from __future__ import annotations

import json
from contextlib import ExitStack, contextmanager
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bootstrap.body_bridge import check_source, compile_source, to_module
from bootstrap.build_machines import bundle_source
from bootstrap.machine_lower import MachineGraph, MachineJSON
from snekc import driver, ir, runner
from snekc.check import Checker
from snekc.emit_wat import Emitter, emit_module
from snekc.machine_bridge import MachineWire, WasmGraphFactory, WasmMachineBackend
from snekc.machines import Graph, has_await
from snekc.types import (BOOL, I32, I64, F32, F64, NONE, STR, ClassInfo, ClassT,
                        CoroutineT, IteratorT, OptionalT, ListT, TupleT, CallableT, StreamT)
from snekc.wasm import encode


ORDER = '''from snek import i32, checkpoint, run
async def change(values: list[i32]) -> i32:
    await checkpoint()
    values[0] = 9
    return 7
async def main() -> None:
    values = [1]
    print(values[0], await change(values), values[0])
    print(False and await change(values) == 0, True or await change(values) == 0)
    print(await change(values) if True else 100)
    try:
        raise ValueError("🐍\\nquoted \\\" text")
    except ValueError as error:
        await checkpoint()
        print(str(error))
run(main())
'''


@contextmanager
def no_host_machines():
    with ExitStack() as guards:
        for name in ("Graph", "MachineEmitter.emit_machine_python", "MachineEmitter.machine_type_section_python",
                     "MachineEmitter.machine_helpers_python", "MachineEmitter.machine_fields"):
            guards.enter_context(patch("snekc.machines." + name, side_effect=AssertionError("Python machine fallback: " + name)))
        yield


class MachineLoweringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="snek-machines-")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.directory = Path(cls.temp.name)
        cls.checker = ROOT / "bootstrap/build/bodies.wasm"
        cls.binary = cls.directory / "machines.wasm"
        with patch.object(Checker, "run", side_effect=AssertionError("Python checker fallback")), \
             patch("ast.parse", side_effect=AssertionError("Python parser fallback")):
            cls.binary.write_bytes(encode(compile_source(bundle_source(), cls.checker)))
        cls.binaries = [cls.binary]
        if os.environ.get("SNEK_WASM_OPT"):
            optimized = cls.directory / "machines.opt.wasm"
            subprocess.run([*driver.find_wasm_opt(), str(cls.binary), "-o", str(optimized),
                            *driver.WASM_FEATURES, "-O3", "--closed-world"], check=True, capture_output=True)
            cls.binaries.append(optimized)

    def module(self, source):
        with patch.object(Checker, "run", side_effect=AssertionError("Python checker fallback")), \
             patch("ast.parse", side_effect=AssertionError("Python parser fallback")):
            return to_module(check_source(source, self.checker))

    def test_all_example_wat_parity(self):
        seen = set()
        factory = WasmGraphFactory(self.binary)

        def compared(f, exc):
            actual = factory(f, exc)
            reference = Graph(f, exc)
            self.assertEqual(actual.locals, reference.locals, f.name)
            self.assertEqual(actual.states, reference.states, f.name)
            self.assertEqual((actual.entry, actual.end, actual.fail, actual.result),
                             (reference.entry, reference.end, reference.fail, reference.result), f.name)
            seen.add(f.name)
            return actual

        for path in sorted((ROOT / "examples").glob("*.py")):
            with self.subTest(example=path.name):
                module = self.module(path.read_text())
                for component in (False, True):
                    expected = emit_module(module, component=component, graph_factory=Graph)
                    with no_host_machines():
                        actual = emit_module(module, component=component, graph_factory=compared,
                                             machine_backend=WasmMachineBackend(self.binary))
                    self.assertEqual(actual, expected, path.name)
        # Runtime-generated machines go through the exact same hook.
        for name in ("snek.checkpoint", "snek.sys.stream_read", "snek.sys.stream_drain", "snek.lib.sleep"):
            self.assertIn(name, seen)
        print(f"  exact core/component WAT and CFG parity: {len(seen)} distinct machine functions")

    def test_executable_raw_and_optimized(self):
        names = ("async_order", "generator_cleanup", "generator_reentrant", "generator_values", "closures_suspended",
                 "suspended_methods", "keyed_async", "context_managers", "stdlib_streams")
        sources = [ORDER] + [(ROOT / f"examples/{name}.py").read_text() for name in names]
        for index, source in enumerate(sources):
            module = self.module(source)
            reference = self.directory / "reference.wasm"
            reference.write_bytes(encode(emit_module(module, graph_factory=Graph)))
            expected = runner.run_binary(reference, stdin=b"one\ntwo\n")
            self.assertEqual(expected.code, 0, expected.stderr)
            if index == 0:
                self.assertEqual(expected.stdout, b'1 7 9\nFalse True\n7\n\xf0\x9f\x90\x8d\nquoted " text\n')
            for lowerer in self.binaries:
                with self.subTest(index=index, lowerer=lowerer.name), \
                     no_host_machines():
                    wat = emit_module(module, graph_factory=WasmGraphFactory(lowerer),
                                      machine_backend=WasmMachineBackend(lowerer))
                path, binary = self.directory / "program.wat", self.directory / "program.wasm"
                path.write_text(wat)
                driver.assemble(path, binary, optimize=lowerer != self.binary)
                actual = runner.run_binary(binary, stdin=b"one\ntwo\n")
                self.assertEqual(actual, expected)

    def test_direct_statement_operands(self):
        # Direct IR isolates Print.args and captures an earlier read before a
        # later Seq mutates it; source checking can otherwise hide this gap.
        exc = ClassInfo("BaseException", None)
        child = ir.Call(CoroutineT(STR), "child", [])
        f = ir.Func("test", [], CoroutineT(NONE), {"x": STR}, [
            ir.Print([ir.LocalGet(STR, "x", STR), ir.Seq(STR,
                [ir.SetLocal("x", ir.StrLit(STR, "changed"))], ir.Await(STR, child))])])
        wire = MachineWire()
        request = wire.request(f, exc)
        owned = wire.graph(json.loads(MachineGraph(MachineJSON(json.dumps(request)).read()).tree().json()))
        reference = Graph(f, exc)
        self.assertEqual(owned.states, reference.states)
        for lowerer in self.binaries:
            graph = WasmGraphFactory(lowerer)(f, exc)
            self.assertEqual(graph.states, reference.states)
            for state in graph.states:
                if state.kind != "await": self.assertFalse(has_await(state.stmt))
            # Follow the linear path: capture x before the reassignment.
            statements, pc = [], graph.entry
            while pc != graph.end:
                statements.append(graph.states[pc].stmt)
                pc = graph.states[pc].next
            self.assertIsInstance(statements[0], ir.SetLocal)
            self.assertEqual(statements[0].value, ir.LocalGet(STR, "x", STR))
            self.assertEqual(statements[1], ir.SetLocal("x", ir.StrLit(STR, "changed")))

    def test_wire_metadata_roundtrip(self):
        wire = MachineWire()
        values = [ir.Const(I32, 2**63 - 1), ir.Const(BOOL, True), ir.StrLit(STR, '\x00\t\n"\\🐍'),
                  (ir.Const(I32, -3), ir.Const(I32, 0))]
        self.assertEqual(wire.unpack(json.loads(json.dumps(wire.pack(values), ensure_ascii=False))), values)
        for bad in ('{"_type":"MachineRequest","version":2}', '{}', '[] trailing',
                    '{"_type":"a","_type":"b"}', '"\\x"', '01', '[1,]'):
            with self.subTest(request=bad):
                result = runner.run_binary(self.binary, stdin=bad.encode())
                self.assertNotEqual(result.code, 0)
                self.assertIn(b"ValueError", result.stderr)

    def test_selected_lowerer_fails_closed(self):
        module = self.module("print(1)\n")
        with patch.dict(os.environ, SNEK_MACHINE_LOWERER=str(self.directory / "missing.wasm")), \
             patch("snekc.machines.Graph", side_effect=AssertionError("Python CFG fallback")):
            with self.assertRaisesRegex(ValueError, "cannot run Snek machine lowerer"): emit_module(module)
        with patch.dict(os.environ, SNEK_MACHINE_LOWERER=str(self.binary)), \
             patch("snekc.machines.Graph", side_effect=AssertionError("Python CFG fallback")):
            self.assertIn("snek.checkpoint", emit_module(module))

    def test_frame_types_and_protocol_helpers(self):
        elements = [I32, I64, F32, F64, BOOL, STR, OptionalT(STR), ListT(STR),
                    TupleT((I32, STR)), CallableT((I32,), STR), StreamT("BinaryInput"),
                    ClassT(ClassInfo("Example", None))]
        types = [IteratorT(t) for t in elements] + [CoroutineT(t) for t in [NONE, *elements]]
        for binary in self.binaries:
            module = ir.Module([], [], "main")
            reference = Emitter(module)
            owned = Emitter(module, machine_backend=WasmMachineBackend(binary))
            for ty in types:
                reference.heap(ty)
                owned.heap(ty)
            helpers = reference.machine_helpers()
            layouts = reference.machine_type_section()
            with no_host_machines():
                self.assertEqual(owned.machine_helpers(), helpers)
                self.assertEqual(owned.machine_type_section(), layouts)
            self.assertEqual(owned.lit_index, reference.lit_index)

    def test_emitter_selection_and_failures(self):
        module = self.module(ORDER)
        expected = emit_module(module)
        # Each selector is independent; frame emission also accepts the Python
        # reference graph without requiring the compiled CFG pass.
        with no_host_machines():
            self.assertEqual(emit_module(module, graph_factory=Graph,
                                         machine_backend=WasmMachineBackend(self.binary)), expected)
        with patch.dict(os.environ, SNEK_MACHINE_EMITTER=str(self.binary), SNEK_MACHINE_LOWERER=str(self.binary)), \
             no_host_machines():
            self.assertEqual(emit_module(module), expected)
        with patch.dict(os.environ, SNEK_MACHINE_EMITTER=str(self.directory / "missing.wasm")), \
             patch("snekc.machines.MachineEmitter.emit_machine_python", side_effect=AssertionError("fallback")):
            with self.assertRaisesRegex(ValueError, "cannot run Snek machine emitter"):
                emit_module(module)
        backend = WasmMachineBackend(self.binary)
        with self.assertRaisesRegex(ValueError, "unknown machine emission request"):
            backend.emit("UnknownRequest")
        result = runner.run_binary(self.binary, stdin=b'{"_type":"FrameRequest","version":2}')
        self.assertNotEqual(result.code, 0)
        self.assertIn(b"unsupported machine emission version", result.stderr)
        self.assertEqual(result.stdout, b"")
        bad = runner.BinaryRunResult(0, b'{"_type":"MachineWAT","version":2,"wat":""}', b"")
        with patch("snekc.runner.run_binary", return_value=bad):
            with self.assertRaisesRegex(ValueError, "unsupported machine emission response"):
                backend.emit("FrameRequest")


if __name__ == "__main__":
    unittest.main(verbosity=2)
