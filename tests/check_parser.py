"""Differential tests for the source frontend written in Snek.

CPython ast is an oracle here only; neither parser.py nor its Wasm imports it.
Run with Python 3.12+ and SNEK_RUNTIME=wasmtime-py, wasmtime, or node.
"""
from __future__ import annotations

import ast
import json
import os
import random
import string
import sys
import tempfile
import unittest
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bootstrap.parser import Lexer, parse, utf8
from bootstrap.bootstrap import assemble
from snekc import driver, runner
from snekc.builtins import BUILTINS_SOURCE, STR_METHODS_TEMPLATE
from snekc.formatting import FORMAT_SOURCE
from snekc.unicode import FLAG_NAMES
from snekc.wasm import encode


def reference(value):
    if isinstance(value, ast.Constant):
        return {"_type": "Constant", "value": literal(value.value)}
    if isinstance(value, ast.FormattedValue):
        spec = value.format_spec
        if isinstance(spec, ast.Constant):
            spec = ast.JoinedStr(values=[spec])
        return {"_type": "FormattedValue", "value": reference(value.value),
                "conversion": value.conversion, "format_spec": reference(spec)}
    if isinstance(value, ast.AST):
        return {"_type": type(value).__name__, **{
            key: reference(item) for key, item in ast.iter_fields(value)
            if key not in {"ctx", "kind", "type_comment", "type_ignores", "type_params"}
        }}
    if isinstance(value, list):
        return [reference(item) for item in value]
    return value


def literal(value):
    if type(value) is float:
        return {"float": value.hex()}
    if value is Ellipsis:
        return {"_type": "Ellipsis"}
    return value


def normalized(value):
    if isinstance(value, list):
        return [normalized(item) for item in value]
    if isinstance(value, dict):
        if value.get("_type") == "Number":
            text = value["text"].replace("_", "")
            if text.lower().startswith(("0x", "0o", "0b")):
                return int(text, 0)
            if "." in text or "e" in text.lower():
                return literal(float(text))
            return int(text, 10)
        return {key: normalized(item) for key, item in value.items()
                if key not in {"_line", "_column"}}
    return value


VALID = [
    "", "# comment without newline", "\ufeff# BOM\nx = 1\r\n",
    "x = (1 +\n # indented comment\n 2)\ny = 3 + \\\n 4\n",
    "if True:\n\tpass\n\t# comment\n\tif False:\n\t\tpass\nx=0\n",
    "if True: pass; x = 1\nelif False: pass\nelse: pass\n",
    "def f(a: i32 = 1, /, b: str = 'x', *args: i32, c: i32, d: i32 = 2, **kw: str) -> None:\n pass\n",
    "f = lambda a=1, /, *args, b=2, **kw: (a, b)\n",
    "@final\n@factory(1)\nclass C(A, metaclass=M):\n @decorator\n async def f(self, x: list[i32]) -> None:\n  await g(x)\n",
    "from ..mod import (a as b, c,)\nimport a.b as c, d\nfrom x import *\n",
    "a = b = 1\n(a): i32\na.b += 2\na[::2] = b\na, *b = c\ndel a, b[1]\n",
    "for a, b in values:\n pass\nelse:\n pass\nwhile True: break\nelse: pass\n",
    "async for x in xs:\n continue\nasync with a as b, c: pass\nwith (a as b, c as d,): pass\n",
    "try:\n raise E('x') from cause\nexcept (A, B) as e:\n assert e, 'bad'\nelse:\n pass\nfinally:\n pass\n",
    "def f():\n global a, b\n nonlocal c\n yield\n yield 1, 2\n yield from xs\n x = yield 2\n return\n",
    "a = f(1, *xs, k=2, **kw)\nb = [1, *xs]\nc = {1: 'a', **d}\nd = (1,)\ne = a[1:2:3, ..., :4]\n",
    "x = -2 ** -3 ** 4 + ~5 // 2 % 3 << 2 & 7 ^ 3 | 1\ny = not x == y < z is not None not in a and b or c\nz = x if a or b else y if c else z\n",
    "x = 0x_FF + 0b_101 + 0o_77 + 00_0 + 12_345\ny = .5 + 1. + 1_2.3_4e-5_6 + 1e400\n",
    "x = 'a' r'\\n' u'雪' '''\ntriple\n'''\ny = '\\a\\b\\f\\n\\r\\t\\v\\x00\\u00e9\\U0001f600\\777\\z'\n",
    "x = f'{{{a!r:>10}}}' 'suffix' f'{b!s} {c!a} {d=}'\n",
    "x = f'{value:{width}.{precision}f}'\ny = rf'raw\\n{a + 1}'\n",
    'x = f"{d["key"]} {"inner"}"\n',
    "x = f'''{\n  a # comment\n  + b\n}'''\n",
    "x = f'{a == b}/{a != b}/{a <= b}/{a >= b}/{a is not None}'\n",
    'x = 1.0.hex()\ny = 0x1.bit_length()\n',
    'with (a) as b: pass\nwith (a).b: pass\nwith (a, b): pass\n',
    'x = f"{f"{a["x"]}"}"\ny = f"{a:{{}}}"\nz = f"{a:{b:{c}}}"\n',
    'x = f"\\\\{a} \\{b}"\ny = f"{a,b}"\n',
    'x = f"{\na\n}"\ny = f"{a:\\\n}"\n',
]

INVALID = [
    "if True:\npass\n", "if True:\n  pass\n pass\n", " x = 1\n",
    "if True:\n\tpass\n        pass\n", "if True:\n pass\n\tpass\n",
    "x = (1]\n", "x = [1\n", "x = 1)\n", "x = 1 \\ x\n", "x = 1 \\\n",
    "x = 'unterminated\n", "x = '''unterminated\n", "x = '\\x1'\n", "x = '\\u12zz'\n",
    "x = 01\n", "x = 1__0\n", "x = 1_\n", "x = 0x\n", "x = 0b2\n", "x = 1e+\n",
    "x = 1._0\n", "x = 1e_2\n", "x = 0x__f\n", "x = 1abc\n",
    "1 = a\n", "a + b = c\n", "a, b: i32\n", "f() += 1\n",
    "if x pass\n", "try: pass\n", "try: pass\nelse: pass\n", "@f\nx = 1\n",
    "def f(a=1, b): pass\n", "def f(*): pass\n", "def f(*a, *b): pass\n",
    "f(x=1, 2)\n", "f(**kw, *xs)\n", "x = not\n", "x = 1 + not 2\n",
    "x = f'}'\n", "x = f'{a'\n", "x = f'{}'\n", "x = f'{a!q}'\n", "x = f'{a b}'\n",
    "x = \x00\n",
    'x = f"{a:"}"\n', 'x = f"{a:\n}"\n',
    'del *a\n', 'x = (yield from a,b)\n', 'from x import (*,)\n',
    'import *\n', 'from x import *, a\n', 'from x import * as a\n',
    '# comment\x00\n', 'x = "\x00"\n',
]


class ParserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="snek-parser-")
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.wasm = Path(cls.tmp.name) / "parser.wasm"
        driver.build(ROOT / "bootstrap/parser.py", cls.wasm, optimize=False)

    def wasm_parse(self, source):
        data = source.encode("utf-8") if isinstance(source, str) else source
        result = runner.run_binary(self.wasm, stdin=data)
        self.assertEqual(result.code, 0, result.stderr.decode(errors="replace"))
        self.assertEqual(result.stderr, b"")
        return json.loads(result.stdout)

    def compare(self, source, *, wasm=True):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SyntaxWarning)
            expected = reference(ast.parse(source.lstrip("\ufeff")))
        owned = json.loads(parse(source).json())
        self.assertEqual(normalized(owned), expected)
        if wasm:
            self.assertEqual(self.wasm_parse(source), owned)

    def test_language_corpus(self):
        files = [ROOT / "bootstrap/encode.py", ROOT / "bootstrap/parser.py",
                 *sorted((ROOT / "examples").glob("*.py")),
                 *sorted((ROOT / "tests/errors").glob("*.py")),
                 *sorted((ROOT / "bench/programs").glob("*.py"))]
        for path in files:
            with self.subTest(path=path.relative_to(ROOT)):
                self.compare(path.read_text())

    def test_builtin_sources(self):
        self.compare(BUILTINS_SOURCE)
        self.compare(string.Template(STR_METHODS_TEMPLATE).substitute(FLAG_NAMES) + FORMAT_SOURCE)

    def test_snek_encoder_builds_parser(self):
        encoder = Path(self.tmp.name) / "encoder.wasm"
        driver.build(ROOT / "bootstrap/encode.py", encoder, optimize=False)
        source = (ROOT / "bootstrap/parser.py").read_text()
        wat = driver.compile_to_wat(source)
        binary = assemble(encoder, wat)
        self.assertEqual(binary, encode(wat, debug_names=False))
        parser = Path(self.tmp.name) / "snek-encoded-parser.wasm"
        parser.write_bytes(binary)
        result = runner.run_binary(parser, stdin=source.encode())
        self.assertEqual((result.code, result.stderr), (0, b""))
        self.assertEqual(json.loads(result.stdout), json.loads(parse(source).json()))

    def test_grammar(self):
        for source in VALID:
            with self.subTest(source=source):
                self.compare(source)

    def test_rejections(self):
        for source in INVALID:
            with self.subTest(source=source):
                with self.assertRaises(SyntaxError):
                    ast.parse(source)
                with self.assertRaises(ValueError):
                    parse(source)
                result = runner.run_binary(self.wasm, stdin=source.encode())
                self.assertNotEqual(result.code, 0)
                self.assertEqual(result.stdout, b"")
                self.assertIn(b"line ", result.stderr)

    def test_explicit_subset_boundaries(self):
        for source in ["x = b'bytes'", "x = 1j", "x = {1, 2}", "x = [n for n in xs]",
                       "x = (n := 1)", "x = '\\ud800'", "café = 1"]:
            with self.subTest(source=source):
                ast.parse(source)
                with self.assertRaises(ValueError):
                    parse(source)
                result = runner.run_binary(self.wasm, stdin=source.encode())
                self.assertNotEqual(result.code, 0)
                self.assertEqual(result.stdout, b"")

    def test_utf8(self):
        text = "\x00\x7f\x80é雪😀\U0010ffff"
        self.assertEqual(utf8(list(text.encode())), text)
        for data in [b"\x80", b"\xc0\x80", b"\xc2", b"\xe0\x80\x80", b"\xed\xa0\x80",
                     b"\xf4\x90\x80\x80", b"\xff", b"\xf0abc"]:
            with self.subTest(data=data):
                with self.assertRaises(ValueError):
                    utf8(list(data))
                result = runner.run_binary(self.wasm, stdin=data)
                self.assertNotEqual(result.code, 0)
                self.assertEqual(result.stdout, b"")
                self.assertIn(b"invalid UTF-8", result.stderr)

    def test_named_unicode_escapes(self):
        sources = [
            r"x = '\N{SNOWMAN}\N{LATIN SMALL LETTER E WITH ACUTE}\N{GRINNING FACE}'",
            r"x = '\N{nul}\N{LF}\N{BYTE ORDER MARK}\N{VS256}'",
            r"x = '\N{CJK UNIFIED IDEOGRAPH-4E00}\N{HANGUL SYLLABLE GGWAEGS}'",
            r"x = r'\N{SNOWMAN}'",
            r'''x = f'\N{SNOWMAN}{a}\N{LEFT CURLY BRACKET}\N{RIGHT CURLY BRACKET}' ''',
            r'''x = f'{a:\N{SPACE}>4}' ''',
            r'''x = rf'\N{SNOWMAN}' ''',
        ]
        for source in sources:
            with self.subTest(source=source):
                self.compare(source)
        for name in ["", "NO SUCH UNICODE NAME", "SNOWMAN ", "ſnowman", "CJK UNIFIED IDEOGRAPH-04E00",
                     "CJK UNIFIED IDEOGRAPH-4DFF", "HANGUL SYLLABLE G", "LATIN SMALL LETTER A WITH MACRON AND GRAVE"]:
            source = "x = '\\N{" + name + "}'"
            with self.subTest(name=name):
                with self.assertRaises(SyntaxError):
                    ast.parse(source)
                with self.assertRaises(ValueError):
                    parse(source)
                result = runner.run_binary(self.wasm, stdin=source.encode())
                self.assertNotEqual(result.code, 0)
                self.assertEqual(result.stdout, b"")

    def test_token_locations(self):
        tokens = Lexer('x = "雪😀"; y = 1\n\t# ignored\nz=2\n').scan()
        y = next(t for t in tokens if t.text == "y")
        z = next(t for t in tokens if t.text == "z")
        self.assertEqual((y.line, y.column), (1, 15))
        self.assertEqual((z.line, z.column), (3, 0))
        source = 'x = "雪"\ny = f"😀{x + 1}"\n'
        actual = self.wasm_parse(source)["body"][1]["value"]["values"][1]["value"]["left"]
        expected = ast.parse(source).body[1].value.values[1].value.left
        self.assertEqual((actual["_line"], actual["_column"]), (expected.lineno, expected.col_offset))

    def test_mutated_syntax(self):
        # Deterministic token damage checks both rejection and accepted tree
        # shape, without treating checker errors (e.g. an unbound name) as syntax.
        snippets = ["x=1\n", "x=a.b\n", "x=f(a,b=2)\n", "if x:\n a=1\nelse:\n b=2\n",
                    "def f(a:i32,b:i32=2)->i32:\n return a+b\n", 'x=f"{a!r:10}"\n',
                    "x=[1,2]\n", "x={1:2}\n", "from snek import i32\n", "for a,b in xs:\n pass\n"]
        rng = random.Random(31)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SyntaxWarning)
            for _ in range(10000):
                source = rng.choice(snippets)
                pos = rng.randrange(len(source))
                source = source[:pos] + rng.choice(["", " ", "\n", ":", "*", ",", "(", "=", "0", "#", '"']) + source[pos + 1:]
                try:
                    expected = ast.parse(source)
                except SyntaxError:
                    with self.assertRaises(ValueError, msg=repr(source)):
                        parse(source)
                    continue
                if any(isinstance(node, ast.Set) for node in ast.walk(expected)):
                    continue  # Sets are an explicit subset boundary.
                self.assertEqual(normalized(json.loads(parse(source).json())), reference(expected), repr(source))

    def test_generated_expressions(self):
        rng = random.Random(82351)
        expressions = ["a", "b", "1", "2.5", "None", "'雪'", "f'{a}'"]
        for _ in range(150):
            a, b, c = rng.choices(expressions[-50:], k=3)
            op = rng.choice(["+", "-", "*", "**", "//", "<<", "&", "and", "or", "is", "not in", "<="])
            form = rng.choice([f"({a}) {op} ({b})", f"({a}) if ({b}) else ({c})",
                               f"f(({a}), key=({b}))", f"[({a}), ({b})]", f"not ({a})"])
            expressions.append(form)
        # One module amortizes Wasm runtime startup and exercises a large tree.
        self.compare("\n".join(f"x = {e}" for e in expressions))

    def test_optimized_self_parse(self):
        if not os.environ.get("SNEK_WASM_OPT"):
            self.skipTest("set SNEK_WASM_OPT to exercise an optimized parser")
        optimized = Path(self.tmp.name) / "parser.opt.wasm"
        driver.build(ROOT / "bootstrap/parser.py", optimized, optimize=True)
        source = (ROOT / "bootstrap/parser.py").read_bytes()
        raw = runner.run_binary(self.wasm, stdin=source)
        opt = runner.run_binary(optimized, stdin=source)
        self.assertEqual((opt.code, opt.stderr), (0, b""))
        self.assertEqual(opt.stdout, raw.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
