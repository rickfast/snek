"""Key inference and cached-key lowering invariants."""
from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from check_closures import walk
from snekc import ir
from snekc.check import check_module
from snekc.emit_wat import Emitter
from snekc.keyed_builtins import keyed_builtin_code
from snekc.types import BOOL, I32, STR, CallableT, ListT


class KeyedBuiltinTests(unittest.TestCase):
    def module(self, body):
        return check_module(ast.parse("from snek import i32\n" + body))

    def test_lambda_parameter_and_result_are_inferred(self):
        module = self.module('''
def main() -> list[str]:
    return sorted(["bbb", "a"], key=lambda s: len(s))
''')
        main = next(f for f in module.funcs if f.name == "main")
        op = next(n for n in walk(main.body) if isinstance(n, ir.KeyedListOp))
        self.assertEqual(op.key.ty, CallableT((STR,), I32))
        self.assertEqual(op.ty, ListT(STR))

    def test_none_retains_the_unkeyed_path(self):
        module = self.module('''
def main() -> list[i32]:
    return sorted([3, 1, 2], key=None)
''')
        main = next(f for f in module.funcs if f.name == "main")
        self.assertFalse(any(isinstance(n, ir.KeyedListOp) for n in walk(main.body)))
        self.assertTrue(any(isinstance(n, ir.ListOp) and n.op == "sorted" for n in walk(main.body)))

    def test_sort_evaluates_keys_only_in_the_initial_pass(self):
        emitter = Emitter(self.module(""))
        code = keyed_builtin_code(emitter, "$test", I32, CallableT((I32,), STR), "sorted", ListT(I32))
        self.assertEqual(code.count("call_ref"), 1)
        initial, merge = code.split("(block $done (loop $pass", 1)
        self.assertIn("call_ref", initial)
        self.assertNotIn("call_ref", merge)
        self.assertEqual(code.count("array.new_default"), 4)
        self.assertNotIn("tuple<", code)

    def test_bool_keys_use_packed_unsigned_loads(self):
        emitter = Emitter(self.module(""))
        code = keyed_builtin_code(emitter, "$test", STR, CallableT((STR,), BOOL), "sorted", ListT(STR))
        self.assertIn("array.get_u $arr.bool", code)


if __name__ == "__main__":
    unittest.main()
