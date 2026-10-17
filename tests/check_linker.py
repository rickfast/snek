"""Fast regressions for authored runtime resources and fixed-source bundling."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bootstrap.build_declarations import numeric_helpers
from bootstrap.build_emitter import compiler_source, encoder_source
from bootstrap.linker import flatten, module_source, parser_source, rename_symbols, statements
from bootstrap.parser import parse
from bootstrap.runtime_data import configuration, runtime_functions
from snekc.builtins import BUILTINS_SOURCE, STR_METHODS_SOURCE
from snekc.formatting import FORMAT_SOURCE
from snekc.runtime_sources import load_runtime_source, resolve_unicode_constants
from snekc.unicode import FLAG_NAMES


class RuntimeSourcesTests(unittest.TestCase):
    def test_resources_are_authored_source(self):
        for name in ("exceptions.snek", "strings.snek", "formatting.snek"):
            source = load_runtime_source(name)
            self.assertTrue(parse(source).get("body").items)
            self.assertNotIn("$SPACE", source)
        self.assertIn("class BaseException:", BUILTINS_SOURCE)
        self.assertEqual(load_runtime_source("formatting.snek"), FORMAT_SOURCE)

    def test_unicode_constants_are_token_aware(self):
        source = '# __unicode_SPACE\nx = "__unicode_SPACE"\ny = __unicode_SPACE\n'
        expected = '# __unicode_SPACE\nx = "__unicode_SPACE"\ny = 2\n'
        self.assertEqual(resolve_unicode_constants(source), expected)
        for name, value in FLAG_NAMES.items():
            self.assertEqual(resolve_unicode_constants("x = __unicode_" + name + "\n"), f"x = {value}\n")
        with self.assertRaisesRegex(ValueError, "unknown runtime Unicode constant"):
            resolve_unicode_constants("x = __unicode_NOT_A_FLAG\n")
        with self.assertRaises(ValueError):
            load_runtime_source("../driver.py")

    def test_preparation_preserves_runtime_locations(self):
        authored = load_runtime_source("strings.snek")
        prepared = parse(STR_METHODS_SOURCE).get("body").items
        original = parse(authored).get("body").items
        self.assertEqual([(n.get("name").text, n.line) for n in original],
                         [(n.get("name").text, n.line) for n in prepared])
        source, _, _ = configuration()
        functions = runtime_functions(source)
        self.assertEqual(len(functions), 63)
        self.assertEqual([name for name, _, _ in functions],
                         [n.get("name").text for n in parse(source).get("body").items])


class LinkerTests(unittest.TestCase):
    def test_multiline_imports_functions_and_string_data(self):
        source = '''"""module docs"""
from allowed import (
    thing,
)
def first(
    value: str,
) -> str:
    return """text
from forbidden import something
def fake():
""" + value

def second() -> str:
    return first("ok")

if __name__ == '__main__':
    print(second())
'''
        result = flatten(source, ("allowed",), select=("first",))
        self.assertEqual([s.words[1] for s in statements(result)], ["first"])
        self.assertIn("from forbidden import something", result)
        self.assertNotIn("def second", result)
        self.assertNotIn("from allowed", result)
        self.assertIn("if __name__", flatten(source, ("allowed",), keep_main=True))

    def test_fail_closed(self):
        bad_sources = ["import os\n", "from os import path\n", "from allowed import *\n",
                       "from allowed import thing as other\n", "x = 3\n", "run()\n",
                       "@decorate\ndef f():\n    pass\n", "def f():\n    pass\ndef f():\n    pass\n"]
        for source in bad_sources:
            with self.subTest(source=source), self.assertRaises(ValueError):
                flatten(source, ("allowed",))
        with self.assertRaises(ValueError):
            flatten("def f():\n    pass\n", (), select=("missing",))
        with self.assertRaises(ValueError):
            module_source("not_in_the_manifest")

    def test_namespacing_does_not_rewrite_strings_or_methods(self):
        source = '''def encode() -> str:
    return "encode"
class Encoder:
    def encode(self) -> str:
        return encode()
def call() -> str:
    return Encoder().encode()
'''
        result = rename_symbols(source, {"encode": "wasm_encode", "Encoder": "wasm_Encoder"})
        self.assertIn('return "encode"', result)
        self.assertIn("def wasm_encode()", result)
        self.assertIn("def encode(self)", result)
        self.assertIn("return wasm_encode()", result)
        self.assertIn("wasm_Encoder().encode()", result)

    def test_numeric_selection_and_encoder_names(self):
        selected = [s.words[1] for s in statements(numeric_helpers())]
        self.assertIn("numeric_digit", selected)
        self.assertNotIn("digit", selected)
        self.assertNotIn("Encoder", selected)
        names = [s.words[1] for s in statements(encoder_source())]
        self.assertTrue(all(name.startswith("wasm_") for name in names))

    def test_generated_unicode_is_separate_and_bundled(self):
        handwritten = (ROOT / "bootstrap/parser.py").read_text()
        self.assertNotIn("BEGIN GENERATED UNICODE NAMES", handwritten)
        bundled = parser_source()
        names = [s.words[1] for s in statements(bundled) if s.words[0] == "def"]
        self.assertIn("unicode_name_table", names)
        self.assertIn("unicode_ideograph", names)
        self.assertNotIn("from bootstrap.", bundled)

    def test_full_bundle_has_no_module_imports_or_duplicate_definitions(self):
        source = compiler_source()
        names = [s.words[1] for s in statements(source) if s.words[0] in ("def", "class")]
        self.assertEqual(len(names), len(set(names)))
        self.assertNotIn("\nfrom bootstrap.", source)
        self.assertTrue(source.endswith("emitter_command()\n"))


if __name__ == "__main__":
    unittest.main()
