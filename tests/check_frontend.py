"""End-to-end source frontend parity, AST contexts, Unicode data and CLI tests."""
from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import unicodedata
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bootstrap import parser
from bootstrap.generate_unicode_names import BEGIN, END, VERSION, aliases, generate
from snekc import CompileError, driver, runner
from snekc.check import check_module
from snekc.emit_wat import emit_module
from snekc.frontend import parse_source, select, to_ast


class FrontendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="snek-frontend-")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.directory = Path(cls.temp.name)
        cls.parser_wasm = cls.directory / "parser.wasm"
        driver.build(ROOT / "bootstrap/parser.py", cls.parser_wasm, optimize=False, frontend="cpython")

    def test_wat_parity(self):
        paths = [*sorted((ROOT / "examples").glob("*.py")),
                 *sorted((ROOT / "bench/programs").glob("*.py")),
                 ROOT / "bootstrap/parser.py", ROOT / "bootstrap/encode.py"]
        for path in paths:
            source = path.read_text()
            for component in (False, True):
                if path.parent.name == "bootstrap" and component:
                    continue  # Binary stdin is currently core-only.
                with self.subTest(path=path.name, component=component):
                    expected = driver.compile_to_wat(source, frontend="cpython", component=component)
                    actual = driver.compile_to_wat(source, frontend="snek", component=component)
                    self.assertEqual(actual, expected)

    def test_checker_error_parity(self):
        for path in sorted((ROOT / "tests/errors").glob("*.py")):
            with self.subTest(path=path.name):
                diagnostics = []
                for frontend in ("cpython", "snek"):
                    with self.assertRaises(CompileError) as raised:
                        driver.compile_to_wat(path.read_text(), str(path), frontend=frontend)
                    diagnostics.append(raised.exception.msg)
                self.assertEqual(*diagnostics)

    def test_no_ast_parse_fallback(self):
        source = '''from snek import i32
class Link:
    next: "Link | None"
    def __init__(self, value: "Link | None" = None) -> None:
        self.next = value
def identity(x: "list[i32]") -> "list[i32]":
    return x
print("\\N{SNOWMAN}".upper(), identity([1, 2]), Link().next is None)
'''
        expected = driver.compile_to_wat(source, frontend="cpython")
        with patch("ast.parse", side_effect=AssertionError("CPython parser fallback")), \
             patch("unicodedata.lookup", side_effect=AssertionError("host name lookup")), \
             patch("unicodedata.name", side_effect=AssertionError("host name table")):
            self.assertEqual(driver.compile_to_wat(source, frontend="snek"), expected)
            with self.assertRaises(CompileError):
                driver.compile_to_wat("if True:\npass", frontend="snek")

    def test_ast_contexts(self):
        source = '''a, [b, *c] = values
a[i].field = b
a.field[i] += 1
a: i32 = 1
del a, b[i], c.field
for a, b in xs:
    with manager() as (c, d):
        pass
async for a in xs:
    async with manager() as b: pass
'''
        expected = ast.parse(source)
        for owned in (parser.parse(source), json.loads(parser.parse(source).json())):
            actual = to_ast(owned)
            self.assertEqual(ast.dump(actual), ast.dump(expected))
        assignment = to_ast(parser.parse("a[i].field = v")).body[0].targets[0]
        self.assertIsInstance(assignment.ctx, ast.Store)
        self.assertIsInstance(assignment.value.ctx, ast.Load)
        self.assertIsInstance(assignment.value.value.ctx, ast.Load)
        self.assertIsInstance(assignment.value.slice.ctx, ast.Load)

    def test_compiled_parser_pipeline(self):
        # Execute the Wasm parser, adapt its JSON, then feed the actual checker
        # and emitter. Compare both core ABIs for every example, not only trees.
        for path in sorted((ROOT / "examples").glob("*.py")):
            with self.subTest(path=path.name):
                source = path.read_text()
                result = runner.run_binary(self.parser_wasm, stdin=source.encode())
                self.assertEqual((result.code, result.stderr), (0, b""))
                owned = json.loads(result.stdout)
                for component in (False, True):
                    actual = emit_module(check_module(to_ast(owned), frontend="snek"), component=component)
                    expected = driver.compile_to_wat(source, frontend="cpython", component=component)
                    self.assertEqual(actual, expected)

    def test_expression_mode(self):
        for text in ["Node | None", "list[tuple[str, i32]]", "Callable[[i32], str]", "'Node'", "(a, b)"]:
            with self.subTest(text=text):
                self.assertEqual(ast.dump(parse_source(text, mode="eval", frontend="snek")),
                                 ast.dump(ast.parse(text, mode="eval")))
        for frontend in ("cpython", "snek"):
            with self.assertRaises(CompileError) as raised:
                driver.compile_to_wat('from snek import i32\ndef f(x: "list[") -> None: pass', frontend=frontend)
            self.assertIn("invalid quoted type annotation", raised.exception.msg)
            self.assertEqual(raised.exception.lineno, 2)

    def test_diagnostics_and_selection(self):
        with patch.dict(os.environ, {"SNEK_FRONTEND": "snek"}):
            self.assertEqual(select(), "snek")
            self.assertEqual(select("cpython"), "cpython")
            with self.assertRaises(CompileError) as raised:
                driver.compile_to_wat("if True:\npass", "broken.py")
            self.assertEqual((raised.exception.lineno, raised.exception.col), (2, 0))
            self.assertIn("broken.py:2:1", raised.exception.format("broken.py"))
        with patch.dict(os.environ, {"SNEK_FRONTEND": "typo"}):
            with self.assertRaises(CompileError):
                driver.compile_to_wat("pass")
            driver.compile_to_wat("print(1)", frontend="cpython")

    def test_cli_commands(self):
        source = self.directory / "named.py"
        source.write_text('print("\\N{SNOWMAN}")\n')
        env = dict(os.environ, SNEK_FRONTEND="typo")  # Explicit CLI flag wins.
        for command in ("check", "wat", "build", "run"):
            with self.subTest(command=command):
                args = [sys.executable, "-m", "snekc", command, str(source), "--frontend", "snek"]
                if command in ("build", "run"):
                    args.append("--no-opt")
                result = subprocess.run(args, cwd=ROOT, env=env, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr.decode())
                if command == "run":
                    self.assertEqual(result.stdout, "☃\n".encode())
        source.write_text("if True:\npass\n")
        failed = subprocess.run([sys.executable, "-m", "snekc", "check", str(source), "--frontend", "snek"],
                                cwd=ROOT, env=env, capture_output=True, text=True)
        self.assertEqual(failed.returncode, 1)
        self.assertIn(":2:1: error: syntax error:", failed.stderr)
        self.assertNotIn("Traceback", failed.stderr)

    def test_unicode_data(self):
        source = (ROOT / "bootstrap/parser.py").read_text()
        block = BEGIN + source.split(BEGIN)[1].split(END)[0] + END
        if unicodedata.unidata_version == VERSION:
            self.assertEqual(block, generate())
        digest = hashlib.sha256(parser.unicode_name_table().encode()).hexdigest()
        self.assertIn("# Table SHA-256: " + digest, block)
        token = parser.Token("", "", 1, 0)
        entries = [(name, int(code, 16)) for name, code in
                   (line.split("=") for line in parser.unicode_name_table().splitlines())]
        self.assertEqual(entries, sorted(entries))
        for name, code in entries:
            self.assertEqual(parser.named_unicode(name, token), chr(code))
        for name, code in aliases().items():
            self.assertEqual(parser.named_unicode(name.lower(), token), chr(code))
        if unicodedata.unidata_version == VERSION:
            for code in range(0x110000):
                name = unicodedata.name(chr(code), "")
                if name:
                    self.assertEqual(parser.named_unicode(name, token), chr(code))
        # Sample the entire table plus algorithmic range endpoints through Wasm.
        chosen = random.Random(151).sample(entries, 250)
        chosen.extend(aliases().items())
        chosen.extend((unicodedata.name(chr(c)), c) for c in [0x3400, 0x4E00, 0x9FFF, 0x20000, 0x2A6DF, 0xAC00, 0xD7A3])
        text = "\n".join("x = '\\N{" + name + "}'" for name, _ in chosen)
        result = runner.run_binary(self.parser_wasm, stdin=text.encode())
        self.assertEqual((result.code, result.stderr), (0, b""))
        tree = to_ast(json.loads(result.stdout))
        self.assertEqual([node.value.value for node in tree.body], [chr(code) for _, code in chosen])


if __name__ == "__main__":
    unittest.main(verbosity=2)
