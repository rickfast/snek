"""Differential proof for the owned declaration IR, not function-body checking."""
from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import random
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bootstrap import declarations as port
from bootstrap.build_declarations import bundle_source, declare
from snekc import driver, runner
from snekc.check import Checker
from snekc.errors import CompileError
from snekc.frontend import to_ast
from snekc import types as t


def type_tree(ty):
    kind, name, args = "", "", []
    if isinstance(ty, t.Prim):
        kind = ty.name
    elif ty == t.NONE:
        kind = "None"
    elif ty == t.STR:
        kind = "str"
    elif ty == t.TRACEBACK:
        kind = "TracebackType"
    elif isinstance(ty, t.ClassT):
        kind, name = "class", ty.info.name
    elif isinstance(ty, t.TypeT):
        kind, args = "type", [t.ClassT(ty.info)]
    elif isinstance(ty, t.OptionalT):
        kind, args = "optional", [ty.inner]
    elif isinstance(ty, t.ListT):
        kind, args = "list", [ty.elem]
    elif isinstance(ty, t.TupleT):
        kind, args = "tuple", ty.elems
    elif isinstance(ty, t.DictT):
        kind, args = "dict", [ty.key, ty.value]
    elif isinstance(ty, t.CallableT):
        kind, args = "Callable", [*ty.params, ty.ret]
    elif isinstance(ty, t.IteratorT):
        kind, args = "Iterator", [ty.elem]
    elif isinstance(ty, t.CoroutineT):
        kind, args = "Coroutine", [ty.elem]
    else:
        raise AssertionError(ty)
    return {"_type": "Type", "kind": kind, "name": name, "args": [type_tree(arg) for arg in args]}


def signature(sig):
    return {"_type": "Signature", "params": [
        {"_type": "Parameter", "name": name, "type": type_tree(ty),
         "default": None if default is None else ast.dump(default)}
        for (name, ty), default in zip(sig.params, sig.defaults)
    ], "returns": type_tree(sig.ret)}


def reference(source):
    checked = Checker(ast.parse(source))
    checked.declare_module()
    classes = []
    for cls in checked.class_order:
        classes.append({
            "_type": "Class", "name": cls.name, "base": cls.base.name if cls.base else "",
            "builtin": cls.builtin,
            "final": any(isinstance(d, ast.Name) and d.id == "final" for d in cls.node.decorator_list),
            "fields": [{"_type": "Field", "name": f.name, "type": type_tree(f.ty), "final": f.final,
                        "owner": f.owner.name, "index": f.index,
                        "default": None if f.default is None else ast.dump(f.default)}
                       for f in cls.fields.values()],
            "methods": [{"_type": "Method", "name": m.name, "signature": signature(m.sig),
                         "decl": m.decl.name, "owner": m.owner.name, "slot": m.slot}
                        for m in cls.methods.values()],
            "constructor": signature(checked.ctor_sig(cls)),
        })
    return {"_type": "Declarations", "schema": 1, "body_checking": "pending", "classes": classes,
            "functions": [{"_type": "Function", "name": name, "signature": signature(sig)}
                          for name, (sig, _) in checked.funcs.items()],
            "imported": sorted(checked.imported),
            "library_imports": [{"_type": "ImportBinding", "name": name, "path": list(path)}
                                for name, path in checked.library_imports.items()],
            "main_statements": len(checked.main_stmts)}


def normalize_defaults(value):
    """AST adapter is used only by the test oracle, never the declaration pass."""
    if isinstance(value, list):
        return [normalize_defaults(v) for v in value]
    if not isinstance(value, dict):
        return value
    out = {}
    for key, child in value.items():
        if key == "default" and child is not None:
            child = ast.dump(to_ast({"_type": "Expression", "body": child}).body)
        else:
            child = normalize_defaults(child)
        out[key] = child
    return out


FOCUSED = '''from __future__ import annotations
from snek import i32, i64, f32, f64, Final, final
from typing import Callable, Optional, Iterator, Generator, Coroutine
from types import TracebackType
class Child(Parent):
    links: list["Child | None"]
    values: dict[tuple[str, i32], tuple[bool, f64]]
    def compute(self, n: i64 = -0x8000000000000000) -> str: return "child"
class Parent:
    label: Final[str] = "label"
    def __init__(self, label: str = "label") -> None: self.label = label
    def compute(self, n: i64 = -9223372036854775808) -> str: return "parent"
    def __eq__(self, other: Parent | None) -> bool: return False
    def __enter__(self) -> Parent: return self
    def __exit__(self, cls: type[BaseException] | None, error: BaseException | None,
                 traceback: TracebackType | None) -> bool: return False
@final
class Leaf(Child): pass
def higher(f: Callable[[list[Parent], Optional[str]], tuple[()]], x: f32 = 0.1) -> Coroutine[None, None, str]: pass
def generator() -> Generator[i32, None, None]: yield 1
async def coroutine() -> None: pass
def not_a_generator() -> None:
    def nested() -> Iterator[i32]: yield 1
    value = lambda: (yield 1)
if __name__ == "__main__":
    print("bodies deliberately not checked")
'''


ERRORS = [
    ("class B(A): pass\nclass A(B): pass", "inheritance cycle"),
    ("class B(A): pass\n@final\nclass A: pass", "cannot subclass"),
    ("class A: pass\nclass B: pass\nclass C(A, B): pass", "multiple inheritance"),
    ("class C(unknown): pass", "base class"),
    ("class C(metaclass=type): pass", "class keywords"),
    ("@bad\nclass C: pass", "only the @final"),
    ("class ValueError: pass", "built-in exception"),
    ("def f() -> None: pass\ndef f() -> None: pass", "already defined"),
    ("def print() -> None: pass", "reserved name"),
    ("from snek import read_bytes\ndef read_bytes() -> None: pass", "reserved name"),
    ("def read_bytes() -> None: pass\nfrom snek import read_bytes", "already defined"),
    ("from snek import i32 as integer", "cannot import"),
    ("from .snek import i32", "cannot import"),
    ("from unknown import x", "not supported"),
    ("import unknown", "plain `import`"),
    ("from snek import sys\nclass sys: pass", "reserved name"),
    ("from snek.sys import stdout as len", "reserved name"),
    ("from snek.sys import unknown", "cannot import"),
    ("from snek import sys as a\nfrom snek import os as a", "already imported"),
    ("x = 1", "module-level variables"),
    ("if __name__ == '__main__': pass\nelse: pass", "else branch"),
    ("class C:\n    x: None", "field cannot have type None"),
    ("class C:\n    x: Final", "explicit type"),
    ("class C:\n    __x: str", "reserved"),
    ("class C:\n    x: str\n    x: str", "already declared"),
    ("class A:\n    x: str\nclass B(A):\n    x: str", "already declared"),
    ("class C:\n    x = 1", "type annotation"),
    ("class C:\n    x: str = str(1)", "defaults must be literals"),
    ("class E(Exception):\n    args: str", "reserved on exception"),
    ("class C:\n    def f(self) -> None: pass\n    def f(self) -> None: pass", "defined twice"),
    ("class C:\n    @staticmethod\n    def f() -> None: pass", "method decorators"),
    ("class C:\n    def __repr__(self) -> str: pass", "special method"),
    ("class C:\n    x: str\n    def x(self) -> None: pass", "already a field"),
    ("class C:\n    def f(x: str) -> None: pass", "methods must take"),
    ("class C:\n    def f(self=None) -> None: pass", "self` cannot"),
    ("def f(x) -> None: pass", "needs a type annotation"),
    ("def f(x: None) -> None: pass", "parameter cannot"),
    ("def f(x: str): pass", "return annotation"),
    ("def f(*x: str) -> None: pass", "parameters are not supported"),
    ("@bad\ndef f() -> None: pass", "function decorators"),
    ("def f(x: list[str] = []) -> None: pass", "default values must be literals"),
    ("def f(x: i32) -> None: pass", "must be imported"),
    ("def f(x: int) -> None: pass", "fixed-width"),
    ("def f(x: float) -> None: pass", "use f64"),
    ("def f(x: Missing) -> None: pass", "unknown type"),
    ("def f(x: 'list[') -> None: pass", "invalid quoted"),
    ("def f(x: str | bool) -> None: pass", "only `T | None`"),
    ("def f(x: bool | None) -> None: pass", "only reference"),
    ("def f(x: list[None]) -> None: pass", "not a useful type"),
    ("def f(x: type[str]) -> None: pass", "needs a class type"),
    ("def f(x: dict[str]) -> None: pass", "dict needs two"),
    ("def f(x: dict[list[str], str]) -> None: pass", "dictionary key type"),
    ("def f(x: dict[str, None]) -> None: pass", "concrete type"),
    ("def f(x: tuple[str, ...]) -> None: pass", "fixed length"),
    ("def f(x: tuple[None]) -> None: pass", "concrete type"),
    ("def f(x: Final[str]) -> None: pass", "only allowed on class fields"),
    ("def f(x: set[str]) -> None: pass", "not supported"),
    ("def f(x: Callable[[str], str]) -> None: pass", "must be imported"),
    ("from typing import Callable\ndef f(x: Callable[..., str]) -> None: pass", "explicit parameter types"),
    ("from typing import Callable\ndef f(x: Callable[[None], str]) -> None: pass", "parameter cannot"),
    ("from typing import Iterator\ndef f(x: Iterator[None]) -> None: pass", "use Iterator"),
    ("from typing import Generator\ndef f(x: Generator[str, str, None]) -> None: pass", "use Iterator"),
    ("def f() -> str: yield 'x'", "a generator needs"),
    ("async def f() -> str: yield 'x'", "async generators"),
    ("class C:\n    async def __str__(self) -> str: pass", "async special methods"),
    ("class C:\n    def __str__(self, x: str) -> str: pass", "__str__ requires"),
    ("class C:\n    def __len__(self) -> bool: pass", "__len__ requires"),
    ("class C:\n    def __enter__(self, x: str) -> str: pass", "__enter__ requires"),
    ("class C:\n    def __exit__(self) -> None: pass", "__exit__ requires"),
    ("class C:\n    def __eq__(self, x: str) -> bool: pass", "__eq__ requires"),
    ("class A:\n    def f(self, x: str = 'a') -> str: pass\nclass B(A):\n    def f(self, x: str = 'b') -> str: pass", "different signature"),
]


class DeclarationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="snek-declarations-tests-")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.directory = Path(cls.temp.name)
        cls.source = bundle_source()
        cls.path = cls.directory / "declarations.bundle.py"
        cls.path.write_text(cls.source)
        cls.wasm = cls.directory / "declarations.wasm"
        driver.build(cls.path, cls.wasm, optimize=False, frontend="snek")

    def wasm_result(self, source, wasm=None):
        return runner.run_binary(wasm or self.wasm, stdin=source.encode())

    def compare(self, source, *, wasm=True):
        actual = json.loads(declare(source).tree().json())
        self.assertEqual(normalize_defaults(actual), reference(source))
        if wasm:
            result = self.wasm_result(source)
            self.assertEqual(result.code, 0, result.stderr.decode())
            self.assertEqual(result.stderr, b"")
            self.assertEqual(json.loads(result.stdout), actual)
        return actual

    def test_corpus(self):
        paths = [*sorted((ROOT / "examples").glob("*.py")),
                 *sorted((ROOT / "bench/programs").glob("*.py")),
                 ROOT / "bootstrap/parser.py", ROOT / "bootstrap/encode.py"]
        for path in paths:
            with self.subTest(path=path.name):
                self.compare(path.read_text())

    def test_own_source(self):
        self.compare(self.source)

    def test_focused_types_and_layouts(self):
        data = self.compare(FOCUSED)
        parent, child, leaf = data["classes"][-3:]
        self.assertEqual([c["name"] for c in (parent, child, leaf)], ["Parent", "Child", "Leaf"])
        self.assertEqual([f["index"] for f in child["fields"]], [1, 2, 3])
        self.assertEqual(child["methods"][0]["decl"], "Parent")
        self.assertEqual(child["methods"][0]["owner"], "Child")
        self.assertEqual(leaf["constructor"], parent["constructor"])

    def test_no_python_parser_checker_or_ast_adapter(self):
        expected = declare(FOCUSED).tree().json()
        with patch("ast.parse", side_effect=AssertionError("Python parser fallback")), \
             patch("snekc.frontend.to_ast", side_effect=AssertionError("AST adapter fallback")), \
             patch.object(Checker, "declare_module", side_effect=AssertionError("Python checker fallback")):
            self.assertEqual(declare(FOCUSED).tree().json(), expected)
            result = self.wasm_result(FOCUSED)
            self.assertEqual(result.code, 0, result.stderr)
            self.assertEqual(result.stdout.decode().strip(), expected)

    def test_declaration_errors(self):
        for source, message in ERRORS:
            with self.subTest(source=source):
                with self.assertRaises(CompileError) as expected:
                    reference(source)
                with self.assertRaises(ValueError) as actual:
                    declare(source)
                self.assertIn(message, str(actual.exception))
                self.assertRegex(str(actual.exception), r"^line \d+, column \d+: ")
                if "invalid quoted" not in message:
                    self.assertEqual(str(actual.exception).split(": ", 1)[1], expected.exception.msg)
                result = self.wasm_result(source)
                self.assertNotEqual(result.code, 0)
                self.assertEqual(result.stdout, b"")
                self.assertIn(str(actual.exception), result.stderr.decode())

    def test_existing_declaration_error_fixtures(self):
        count = 0
        for path in sorted((ROOT / "tests/errors").glob("*.py")):
            source = path.read_text()
            try:
                reference(source)
            except CompileError:
                with self.subTest(path=path.name):
                    with self.assertRaises(ValueError):
                        declare(source)
                    result = self.wasm_result(source)
                    self.assertNotEqual(result.code, 0)
                    self.assertEqual(result.stdout, b"")
                count += 1
        self.assertGreaterEqual(count, 25)

    def test_library_aliases(self):
        for source in ("from snek import sys as a, os as a",
                       "from snek.sys import stdout as a, stderr as a",
                       "import snek.sys as a, snek.os as a",
                       "import snek.sys, snek.os\nfrom snek import i32"):
            with self.subTest(source=source):
                self.compare(source)

    def test_default_types_and_integer_boundaries(self):
        for ty, text in [("i32", "-2147483648"), ("i32", "0x7fff_ffff"),
                         ("i64", "-0x8000000000000000"), ("i64", "9223372036854775807"),
                         ("f32", "-0.0"), ("f64", "1e9999999999999999999999999999"),
                         ("bool", "True"), ("str | None", "None"), ("str | None", "'x'")]:
            with self.subTest(ty=ty, text=text):
                self.compare(f"from snek import i32, i64, f32, f64\ndef f(x: {ty} = {text}) -> None: pass")
        for ty, text in [("i32", "2147483648"), ("i32", "-2147483649"),
                         ("i64", "9223372036854775808"), ("i64", "-9223372036854775809"),
                         ("i32", "1.0"), ("f64", "1"), ("i32", "True"),
                         ("bool", "1"), ("str", "None"), ("str | None", "1")]:
            source = f"from snek import i32, i64, f64\ndef f(x: {ty} = {text}) -> None: pass"
            with self.subTest(ty=ty, text=text):
                with self.assertRaises(CompileError):
                    reference(source)
                with self.assertRaises(ValueError):
                    declare(source)
                self.assertNotEqual(self.wasm_result(source).code, 0)
        for source in ["def f(x: str, x: str) -> None: pass",
                       "class C:\n    def f(self, self: str) -> None: pass"]:
            with self.assertRaisesRegex(ValueError, "duplicate parameter"):
                declare(source)
            self.assertNotEqual(self.wasm_result(source).code, 0)

    def test_override_literal_identity(self):
        for ty, left, right in [
            ("i32", "0x10", "16"), ("i32", "+0b100", "+4"),
            ("i64", "-0x8000000000000000", "-9223372036854775808"),
            ("f64", "0.1", "0.10000000000000001"), ("f64", "1e400", "10e399"),
            ("f64", "0.0", "1e-9999999999999999999999"),
            ("f64", "-0.0", "-0e99999999999999999999999"),
            ("f32", "1.0", "1e0"), ("str", "'a'", "'\\x61'"),
        ]:
            source = (f"from snek import i32, i64, f32, f64\nclass A:\n"
                      f"    def f(self, x: {ty} = {left}) -> None: pass\nclass B(A):\n"
                      f"    def f(self, x: {ty} = {right}) -> None: pass\n")
            with self.subTest(left=left, right=right):
                self.compare(source)
        # Default equality is AST equality, not simply equal runtime values.
        for left, right in [("0.0", "-0.0"), ("1.0", "+1.0"), ("1.0", "1.0000000000000002")]:
            source = (f"from snek import f64\nclass A:\n    def f(self, x: f64 = {left}) -> None: pass\n"
                      f"class B(A):\n    def f(self, x: f64 = {right}) -> None: pass")
            with self.assertRaisesRegex(ValueError, "different signature"):
                declare(source)
            self.assertNotEqual(self.wasm_result(source).code, 0)

    def test_numeric_helpers(self):
        rng = random.Random(721)
        numbers = ["0.0", "1.7976931348623157e308", "1.7976931348623159e308",
                   "4.9406564584124654e-324", "2.4703282292062327e-324", "2.4703282292062328e-324",
                   "0.0e999999999999999999999999999999", "1e-99999999999999999999999"]
        numbers += [f"{rng.randrange(1, 10**20)}e{rng.randrange(-345, 310)}" for _ in range(200)]
        for number in numbers:
            with self.subTest(number=number):
                self.assertEqual(port.float_key(number), ",".join(map(str, struct.pack("<d", float(number)))))
        for bits in (32, 64):
            values = [-(2 ** (bits - 1)), 0, 2 ** (bits - 1) - 1]
            values += [rng.randrange(-(2 ** (bits - 1)), 2 ** (bits - 1)) for _ in range(200)]
            for value in values:
                for text in (str(abs(value)), hex(abs(value)), oct(abs(value)), bin(abs(value))):
                    self.assertEqual(port.integer_text(text, value < 0, bits, port.Node("Number")), str(value))

    def test_body_checking_is_explicitly_pending(self):
        source = 'def f() -> str:\n    return undefined + 42\nprint(also_undefined)'
        result = self.compare(source)
        self.assertEqual(result["body_checking"], "pending")
        with self.assertRaises(CompileError):
            driver.compile_to_wat(source)

    def test_optimized(self):
        if not os.environ.get("SNEK_WASM_OPT"):
            self.skipTest("set SNEK_WASM_OPT to include optimized checks")
        optimized = self.directory / "declarations.opt.wasm"
        driver.build(self.path, optimized, optimize=True, frontend="snek")
        for source in (FOCUSED, self.source):
            result = self.wasm_result(source, optimized)
            self.assertEqual(result.code, 0, result.stderr.decode())
            self.assertEqual(json.loads(result.stdout), json.loads(declare(source).tree().json()))


if __name__ == "__main__":
    unittest.main(verbosity=2)
