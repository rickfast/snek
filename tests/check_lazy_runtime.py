"""Lazy runtime parsing, exact locations, and eager/lazy checker parity."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bootstrap.bodies import BodyChecker
from bootstrap.build_declarations import declare
from bootstrap.parser import Node, parse, parse_at, sequence, string
from bootstrap.runtime_data import configuration, indexed_runtime, runtime_functions


class LazyRuntimeTests(unittest.TestCase):
    def checker(self, source, *, eager=False):
        runtime, intrinsics, contract = configuration()
        tree = parse(runtime).put("runtime_abi", string(contract)) if eager else indexed_runtime(runtime, contract)
        return BodyChecker(declare(source), tree, intrinsics)

    def assert_fragments_match(self, source):
        original = parse(source).get("body").items
        fragments = runtime_functions(source)
        self.assertEqual(len(fragments), len(original))
        for (name, line, text), expected in zip(fragments, original):
            nodes = parse_at(text, line).get("body").items
            self.assertEqual(len(nodes), 1)
            self.assertEqual(nodes[0].get("name").text, name)
            self.assertEqual(nodes[0].json(), expected.json())

    def test_all_fixed_helpers_keep_exact_ast_and_locations(self):
        runtime, _, _ = configuration()
        self.assert_fragments_match(runtime)

    def test_index_handles_multiline_strings_comments_and_newlines(self):
        source = '# header\n\ndef first() -> str:\n    return """a\u2028b\n' + 'def not_a_function\n"""\n\n# separator\ndef second() -> str:\n    return "x"\n'
        for text in (source, source.replace("\n", "\r\n"), source.replace("\n", "\r")):
            self.assert_fragments_match(text)

    def test_index_rejects_invalid_or_duplicate_fixed_library(self):
        for source in ('x = 1\n', 'def f() -> None:\n    pass\ndef f() -> None:\n    pass\n',
                       'def f(\n'):
            with self.assertRaises(ValueError):
                runtime_functions(source)

    def test_unused_helpers_are_not_parsed(self):
        checker = self.checker("print(42)\n")
        with patch("bootstrap.bodies.parse_at", side_effect=AssertionError("unused helper parsed")):
            checker.run()
        self.assertTrue(checker.helpers)
        self.assertTrue(all(n.kind == "FunctionSource" for n in checker.helpers.values()))

    def test_used_and_transitive_helpers_are_parsed_once(self):
        checker = self.checker('print("abc".find("b"), "abcd".find("c"))\n')
        with patch("bootstrap.bodies.parse_at", wraps=parse_at) as calls:
            checker.run()
            count = calls.call_count
            self.assertGreater(count, 1)  # find also needs its index-bound helpers
            self.assertLess(count, len(checker.helpers))
            signature = checker.helper_signature("__str_find")
            self.assertIs(checker.helper_signature("__str_find"), signature)
            self.assertEqual(calls.call_count, count)
        self.assertEqual(count, len(checker.helper_sigs))
        self.assertEqual(count, len({(c.args[0], c.args[1]) for c in calls.call_args_list}))
        for name, node in checker.helpers.items():
            self.assertEqual(node.kind, "FunctionDef" if name in checker.helper_sigs else "FunctionSource")

    def test_cache_is_per_checker(self):
        first, second = self.checker("print(1)"), self.checker("print(1)")
        first.helper_signature("__str_find")
        self.assertEqual(second.helpers["__str_find"].kind, "FunctionSource")
        second.helper_signature("__str_find")
        self.assertIsNot(first.helpers["__str_find"], second.helpers["__str_find"])

    def test_eager_and_lazy_typed_ir_are_identical(self):
        for name in ("str_methods.py", "format_specs.py", "runtime_checker.py", "str_codepoints.py"):
            source = (ROOT / "examples" / name).read_text()
            eager, lazy = self.checker(source, eager=True), self.checker(source)
            eager.run()
            lazy.run()
            self.assertEqual(lazy.tree().json(), eager.tree().json(), name)

    def test_fragment_syntax_error_keeps_original_location(self):
        source = 'def bad() -> str:\n    return @\n'
        with self.assertRaises(ValueError) as full:
            parse("\n" * 39 + source)
        with self.assertRaises(ValueError) as fragment:
            parse_at(source, 40)
        self.assertEqual(str(fragment.exception), str(full.exception))

    def test_corrupt_index_fails_closed(self):
        for source in ("def other() -> None:\n    pass\n", "print(1)\n",
                       "def expected() -> None:\n    pass\ndef other() -> None:\n    pass\n"):
            node = Node("FunctionSource", source, 20).put("name", string("expected"))
            runtime = Node("Module").put("runtime_abi", string("test")).put("body", sequence([node]))
            checker = BodyChecker(declare(""), runtime, {})
            with self.assertRaisesRegex(ValueError, "invalid indexed runtime helper expected"):
                checker.helper_signature("expected")


if __name__ == "__main__":
    unittest.main()
