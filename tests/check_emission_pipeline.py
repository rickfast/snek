"""Repeatable owned emission and direct self-hosted build orchestration."""
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bootstrap.build_bodies import check
from bootstrap.emit_module import emit_source_module
from snekc import driver, selfhost
from snekc.errors import CompileError
from snekc.project import Project
from snekc.wasm import encode


class RepeatEmissionTests(unittest.TestCase):
    def test_gather_results_survive_owned_bridge_and_repeat_emission(self):
        import json
        from bootstrap.body_bridge import to_module
        from snekc.emit_wat import emit_module
        source = (ROOT / "examples/gather.py").read_text()
        owned = check(source)
        before = owned.tree().json()
        reference = to_module(json.loads(before))
        prelude = (ROOT / "snekc/prelude.wat").read_text()
        for build in (lambda: emit_source_module(owned, prelude), lambda: emit_module(reference)):
            wat = build()
            self.assertIn("$snek.gather<", wat)
            self.assertEqual(build(), wat)
            self.assertTrue(encode(wat).startswith(b"\0asm"))
        self.assertEqual(owned.tree().json(), before)

    def test_wait_groups_survive_owned_bridge_and_repeat_emission(self):
        import json
        from bootstrap.body_bridge import to_module
        from snekc.emit_wat import emit_module
        source = (ROOT / "examples/wait_all.py").read_text()
        owned = check(source)
        before = owned.tree().json()
        reference = to_module(json.loads(before))
        prelude = (ROOT / "snekc/prelude.wat").read_text()
        for build in (lambda: emit_source_module(owned, prelude), lambda: emit_module(reference)):
            wat = build()
            self.assertIn("$snek.wait_all<", wat)
            self.assertEqual(build(), wat)
            self.assertTrue(encode(wat).startswith(b"\0asm"))
        self.assertEqual(owned.tree().json(), before)
        for build in (lambda: emit_source_module(owned, prelude, component=True),
                      lambda: emit_module(reference, component=True)):
            with self.assertRaisesRegex(ValueError, "wait_all.*not yet supported for components"):
                build()

    def test_list_handles_survive_owned_bridge_and_repeat_emission(self):
        import json
        from bootstrap.body_bridge import to_module
        from snekc.emit_wat import emit_module
        from check_library import NESTED_LIST_SOURCE, NESTED_LIST_EXPORTS
        owned = check(NESTED_LIST_SOURCE)
        before = owned.tree().json()
        reference = to_module(json.loads(before))
        prelude = (ROOT / "snekc/prelude.wat").read_text()
        for build in (lambda: emit_source_module(owned, prelude, exports=NESTED_LIST_EXPORTS),
                      lambda: emit_module(reference, exports=NESTED_LIST_EXPORTS)):
            wat = build()
            self.assertIn('(export "__snek_list_get_0")', wat)
            self.assertEqual(build(), wat)
            self.assertTrue(encode(wat).startswith(b"\0asm"))
        self.assertEqual(owned.tree().json(), before)

    def test_class_handles_survive_owned_bridge_and_repeat_emission(self):
        import json
        from bootstrap.body_bridge import to_module
        from snekc.emit_wat import emit_module
        from check_library import CLASS_VALUE_SOURCE, CLASS_VALUE_EXPORTS
        owned = check(CLASS_VALUE_SOURCE)
        before = owned.tree().json()
        reference = to_module(json.loads(before))
        prelude = (ROOT / "snekc/prelude.wat").read_text()
        for build in (lambda: emit_source_module(owned, prelude, exports=CLASS_VALUE_EXPORTS),
                      lambda: emit_module(reference, exports=CLASS_VALUE_EXPORTS)):
            wat = build()
            self.assertIn('(export "__snek_class_test_0")', wat)
            self.assertEqual(build(), wat)
            self.assertTrue(encode(wat).startswith(b"\0asm"))
        self.assertEqual(owned.tree().json(), before)

    def test_list_callback_types_survive_owned_bridge_and_repeat_emission(self):
        import json
        from bootstrap.body_bridge import to_module
        from snekc.emit_wat import emit_module
        from check_library import LIST_CALLBACK_SOURCE, LIST_CALLBACK_EXPORTS
        owned = check(LIST_CALLBACK_SOURCE)
        before = owned.tree().json()
        reference = to_module(json.loads(before))
        prelude = (ROOT / "snekc/prelude.wat").read_text()
        for build in (lambda: emit_source_module(owned, prelude, exports=LIST_CALLBACK_EXPORTS),
                      lambda: emit_module(reference, exports=LIST_CALLBACK_EXPORTS)):
            wat = build()
            self.assertIn('(export "__snek_list_get_0")', wat)
            self.assertEqual(build(), wat)
            self.assertTrue(encode(wat).startswith(b"\0asm"))
        self.assertEqual(owned.tree().json(), before)

    def test_async_callback_types_survive_owned_bridge_and_repeat_emission(self):
        import json
        from bootstrap.body_bridge import to_module
        from snekc.emit_wat import emit_module
        from check_library import ASYNC_CALLBACK_SOURCE, ASYNC_CALLBACK_EXPORTS
        owned = check(ASYNC_CALLBACK_SOURCE)
        before = owned.tree().json()
        reference = to_module(json.loads(before))
        prelude = (ROOT / "snekc/prelude.wat").read_text()
        for build in (lambda: emit_source_module(owned, prelude, exports=ASYNC_CALLBACK_EXPORTS),
                      lambda: emit_module(reference, exports=ASYNC_CALLBACK_EXPORTS)):
            wat = build()
            self.assertIn('(import "snek_callback_async" "0"', wat)
            self.assertEqual(build(), wat)
            self.assertTrue(encode(wat).startswith(b"\0asm"))
        self.assertEqual(owned.tree().json(), before)

    def test_callback_lists_survive_owned_bridge_and_repeat_emission(self):
        import json
        from bootstrap.body_bridge import to_module
        from snekc.emit_wat import emit_module
        from check_library import CALLBACK_LIST_SOURCE, CALLBACK_LIST_EXPORTS
        owned = check(CALLBACK_LIST_SOURCE)
        before = owned.tree().json()
        reference = to_module(json.loads(before))
        prelude = (ROOT / "snekc/prelude.wat").read_text()
        for build in (lambda: emit_source_module(owned, prelude, exports=CALLBACK_LIST_EXPORTS),
                      lambda: emit_module(reference, exports=CALLBACK_LIST_EXPORTS)):
            wat = build()
            self.assertIn('(export "__snek_list_get_0")', wat)
            self.assertIn('(import "snek_callback_async"', wat)
            self.assertEqual(build(), wat)
            self.assertTrue(encode(wat).startswith(b"\0asm"))
        self.assertEqual(owned.tree().json(), before)

    def test_dictionary_handles_survive_owned_bridge_and_repeat_emission(self):
        import json
        from bootstrap.body_bridge import to_module
        from snekc.emit_wat import emit_module
        from check_library import DICT_SOURCE, DICT_EXPORTS
        owned = check(DICT_SOURCE)
        before = owned.tree().json()
        reference = to_module(json.loads(before))
        prelude = (ROOT / "snekc/prelude.wat").read_text()
        for build in (lambda: emit_source_module(owned, prelude, exports=DICT_EXPORTS),
                      lambda: emit_module(reference, exports=DICT_EXPORTS)):
            wat = build()
            self.assertIn('(export "__snek_dict_get_0")', wat)
            self.assertEqual(build(), wat)
            self.assertTrue(encode(wat).startswith(b"\0asm"))
        self.assertEqual(owned.tree().json(), before)

    def test_higher_callbacks_survive_owned_bridge_and_repeat_emission(self):
        import json
        from bootstrap.body_bridge import to_module
        from snekc.emit_wat import emit_module
        from check_library import HIGHER_CALLBACK_SOURCE, HIGHER_CALLBACK_EXPORTS
        owned = check(HIGHER_CALLBACK_SOURCE)
        before = owned.tree().json()
        reference = to_module(json.loads(before))
        prelude = (ROOT / "snekc/prelude.wat").read_text()
        for build in (lambda: emit_source_module(owned, prelude, exports=HIGHER_CALLBACK_EXPORTS),
                      lambda: emit_module(reference, exports=HIGHER_CALLBACK_EXPORTS)):
            wat = build()
            self.assertIn('(import "snek_callback_async"', wat)
            self.assertEqual(build(), wat)
            self.assertTrue(encode(wat).startswith(b"\0asm"))
        self.assertEqual(owned.tree().json(), before)

    def test_callback_types_survive_owned_bridge_and_repeat_emission(self):
        import json
        from bootstrap.body_bridge import to_module
        from snekc.emit_wat import emit_module
        from check_library import CALLBACK_SOURCE, CALLBACK_EXPORTS
        owned = check(CALLBACK_SOURCE)
        before = owned.tree().json()
        reference = to_module(json.loads(before))
        prelude = (ROOT / "snekc/prelude.wat").read_text()
        for build in (lambda: emit_source_module(owned, prelude, exports=CALLBACK_EXPORTS),
                      lambda: emit_module(reference, exports=CALLBACK_EXPORTS)):
            wat = build()
            self.assertIn('(import "snek_callback" "0"', wat)
            self.assertIn('(export "__snek_wrap_0")', wat)
            self.assertEqual(build(), wat)
            self.assertTrue(encode(wat).startswith(b"\0asm"))
        self.assertEqual(owned.tree().json(), before)

    def test_host_object_types_survive_owned_bridge_and_repeat_emission(self):
        import json
        from bootstrap.body_bridge import to_module
        from snekc.emit_wat import emit_module
        from check_library import OBJECT_SOURCE, OBJECT_EXPORTS
        owned = check(OBJECT_SOURCE)
        before = owned.tree().json()
        reference = to_module(json.loads(before))
        prelude = (ROOT / "snekc/prelude.wat").read_text()
        for build in (lambda: emit_source_module(owned, prelude, exports=OBJECT_EXPORTS),
                      lambda: emit_module(reference, exports=OBJECT_EXPORTS)):
            wat = build()
            self.assertIn('(import "snek_object" "same"', wat)
            self.assertIn('(ref extern)', wat)
            self.assertEqual(build(), wat)
            self.assertTrue(encode(wat).startswith(b"\0asm"))
        self.assertEqual(owned.tree().json(), before)

    def test_host_metadata_survives_body_bridge_and_repeat_emission(self):
        import json
        from bootstrap.body_bridge import to_module
        from snekc.emit_wat import emit_module
        source = 'from snek import host\n@host("text")\ndef text() -> str:\n    pass\n'
        source += '@host("async_text")\nasync def async_text() -> str:\n    pass\n'
        owned = check(source)
        before = owned.tree().json()
        reference = to_module(json.loads(before))
        self.assertEqual(next(f.host for f in reference.funcs if f.name == "text"), "text")
        prelude = (ROOT / "snekc/prelude.wat").read_text()
        for build in (lambda: emit_source_module(owned, prelude, exports=["text", "async_text"]),
                      lambda: emit_module(reference, exports=["text", "async_text"])):
            wat = build()
            self.assertIn('(import "snek_host" "text"', wat)
            self.assertIn('(import "snek_host_async" "async_text"', wat)
            self.assertEqual(build(), wat)
            self.assertTrue(encode(wat).startswith(b"\0asm"))
        self.assertEqual(owned.tree().json(), before)
        self.assertFalse(reference.unicode.dynamic)

    def test_library_unicode_policy_does_not_mutate_checked_program(self):
        import ast
        from snekc.check import check_module
        from snekc.emit_wat import emit_module
        source = 'def identity(value: str) -> str:\n    return value\n'
        prelude = (ROOT / "snekc/prelude.wat").read_text()
        owned = check(source)
        before = owned.tree().json()
        command = emit_source_module(owned, prelude)
        library = emit_source_module(owned, prelude, exports=["identity"])
        self.assertIn('(export "identity")', library)
        self.assertEqual(owned.tree().json(), before)
        self.assertEqual(emit_source_module(owned, prelude), command)
        reference = check_module(ast.parse(source))
        command = emit_module(reference)
        self.assertIn('(export "identity")', emit_module(reference, exports=["identity"]))
        self.assertFalse(reference.unicode.dynamic)
        self.assertEqual(emit_module(reference), command)

    def test_repeated_and_mixed_core_component_emission_preserves_checked_ir(self):
        prelude = (ROOT / "snekc/prelude.wat").read_text()
        sources = (
            'print(41 + 1)\n',
            'from snek import i32, checkpoint, run\n'
            'async def value() -> i32:\n    await checkpoint()\n    return 42\n'
            'print(run(value()))\n',
            (ROOT / "examples/closures_suspended.py").read_text(),
        )
        for source in sources:
            expected = {mode: emit_source_module(check(source), prelude, mode) for mode in (False, True)}
            for modes in ((False, False, True, True, False), (True, True, False, False, True)):
                with self.subTest(source=source[:60], modes=modes):
                    checked = check(source)
                    before = checked.tree().json()
                    for mode in modes:
                        actual = emit_source_module(checked, prelude, mode)
                        self.assertEqual(actual, expected[mode])
                        self.assertEqual(checked.tree().json(), before)
                        self.assertTrue(encode(actual).startswith(b"\0asm\1\0\0\0"))


class DirectBuildTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="snek-direct-build-")
        self.addCleanup(directory.cleanup)
        self.source = Path(directory.name, "program.py")
        self.source.write_text("print(42)\n")
        self.output = Path(directory.name, "program.wasm")
        self.binary = encode("(module)")

    def test_direct_build_calls_compiler_once_without_host_passes(self):
        with patch.object(selfhost, "invoke", return_value=self.binary) as invoke, \
             patch.object(driver, "compile_to_wat", side_effect=AssertionError("WAT route")), \
             patch.object(driver, "encode", side_effect=AssertionError("host encoder")), \
             patch.object(driver, "find_wasm_opt", side_effect=AssertionError("optimizer")):
            result = driver.build(self.source, self.output, optimize=False, compiler="snek")
        invoke.assert_called_once_with(Project(self.source, self.source.read_text()).envelope().encode(), ["--project"])
        self.assertEqual(self.output.read_bytes(), self.binary)
        self.assertIsNone(result.wat)
        self.assertEqual(result.wasm_path, self.output)

    def test_environment_selection_uses_direct_route(self):
        with patch.dict(os.environ, {"SNEK_COMPILER": "snek"}), \
             patch.object(selfhost, "invoke", return_value=self.binary) as invoke:
            driver.build(self.source, self.output, optimize=False)
        invoke.assert_called_once_with(Project(self.source, self.source.read_text()).envelope().encode(), ["--project"])

    def test_explicit_python_selection_retains_wat(self):
        with patch.dict(os.environ, {"SNEK_COMPILER": "snek"}), \
             patch.object(selfhost, "invoke", side_effect=AssertionError("self-hosted route")):
            result = driver.build(self.source, self.output, optimize=False, compiler="python")
        self.assertEqual(self.output.read_bytes(), encode(result.wat))

    def test_keep_wat_retains_text_route_and_result(self):
        with patch.object(selfhost, "invoke", side_effect=[b"(module)", self.binary]) as invoke:
            result = driver.build(self.source, self.output, optimize=False, keep_wat=True, compiler="snek")
        self.assertEqual([call.args for call in invoke.call_args_list],
                         [(Project(self.source, self.source.read_text()).envelope().encode(), ["--project", "--wat"]), (b"(module)", ["--encode"])])
        self.assertEqual(result.wat, "(module)")
        self.assertEqual(self.output.with_suffix(".wat").read_text(), result.wat)

    def test_direct_binary_uses_shared_optimizer(self):
        def optimize(command, **kwargs):
            self.assertEqual(Path(command[1]).read_bytes(), self.binary)
            self.assertIn("-O3", command)
            self.assertIn("--closed-world", command)
            Path(command[command.index("-o") + 1]).write_bytes(self.binary + b"optimized")
            return subprocess.CompletedProcess(command, 0, "", "")

        with patch.object(selfhost, "invoke", return_value=self.binary), \
             patch.object(driver, "find_wasm_opt", return_value=["test-wasm-opt"]), \
             patch.object(driver.subprocess, "run", side_effect=optimize) as run:
            driver.build(self.source, self.output, compiler="snek")
        run.assert_called_once()
        self.assertEqual(self.output.read_bytes(), self.binary + b"optimized")

    def test_failures_preserve_existing_outputs(self):
        self.output.write_bytes(b"existing binary")
        text = self.output.with_suffix(".wat")
        text.write_text("existing WAT")
        for result in (b"invalid", b"\0asm\x0d\0\x01\0"):
            with self.subTest(result=result), patch.object(selfhost, "invoke", return_value=result):
                with self.assertRaisesRegex(RuntimeError, "core Wasm binary"):
                    driver.build(self.source, self.output, optimize=False, compiler="snek")
        with patch.object(selfhost, "invoke", side_effect=CompileError("bad source")):
            with self.assertRaises(CompileError):
                driver.build(self.source, self.output, optimize=False, compiler="snek")
        with patch.object(selfhost, "invoke", return_value=self.binary), \
             patch.object(driver, "find_wasm_opt", return_value=["test-wasm-opt"]), \
             patch.object(driver.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "", "failed")):
            with self.assertRaisesRegex(driver.ToolError, "wasm-opt failed"):
                driver.build(self.source, self.output, compiler="snek")
        self.assertEqual(self.output.read_bytes(), b"existing binary")
        self.assertEqual(text.read_text(), "existing WAT")

    def test_frontend_and_output_conflicts_fail_before_invocation(self):
        with patch.object(selfhost, "invoke", side_effect=AssertionError("compiler invoked")):
            with self.assertRaisesRegex(ValueError, "owns its frontend"):
                driver.build(self.source, self.output, frontend="cpython", compiler="snek")
            with self.assertRaisesRegex(driver.ToolError, "output paths must be distinct"):
                driver.build(self.source, self.source, compiler="snek")


if __name__ == "__main__":
    unittest.main()
