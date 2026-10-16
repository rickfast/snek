"""Closure-conversion invariants, independent of the selected Wasm runtime."""
from __future__ import annotations

import ast
import sys
import unittest
from dataclasses import fields
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from snekc import ir
from snekc.check import check_module
from snekc.types import CellT, I32


def walk(value):
    if isinstance(value, (ir.Expr, ir.Stmt, ir.Handler)):
        yield value
        for f in fields(value):
            yield from walk(getattr(value, f.name))
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from walk(v)


class ClosureTests(unittest.TestCase):
    def check(self, source):
        return check_module(ast.parse("from snek import i32\nfrom typing import Callable\n" + source))

    def test_only_captured_bindings_are_boxed(self):
        module = self.check('''
def outer(x: i32) -> Callable[[], i32]:
    unrelated = 3
    value = x + unrelated
    return lambda: value
''')
        outer = next(f for f in module.funcs if f.name == "outer")
        cells = {n: t for n, t in outer.locals.items() if isinstance(t, CellT)}
        self.assertEqual(cells, {"cell.value": CellT(I32)})
        self.assertEqual(sum(isinstance(n, ir.CellNew) for n in walk(outer.body)), 1)

    def test_siblings_share_a_single_cell(self):
        module = self.check('''
def outer(x: i32) -> tuple[Callable[[], i32], Callable[[], i32]]:
    def one() -> i32:
        nonlocal x
        x += 1
        return x
    def two() -> i32:
        return x
    return one, two
''')
        outer = next(f for f in module.funcs if f.name == "outer")
        closures = [n for n in walk(outer.body) if isinstance(n, ir.Closure)]
        self.assertEqual(len(closures), 2)
        self.assertEqual([n.captures[0].name for n in closures], ["cell.x", "cell.x"])
        self.assertEqual(sum(isinstance(n, ir.CellNew) for n in walk(outer.body)), 1)
        self.assertFalse(any(isinstance(n, ir.CapturedGet) for f in module.funcs for n in walk(f.body)))

    def test_direct_module_call_stays_direct(self):
        module = self.check('''
def add(x: i32, y: i32 = 1) -> i32:
    return x + y
def main() -> i32:
    return add(y=3, x=2)
''')
        main = next(f for f in module.funcs if f.name == "main")
        calls = [n for n in walk(main.body) if isinstance(n, ir.Call)]
        self.assertEqual([n.func for n in calls], ["add"])
        self.assertFalse(any(isinstance(n, (ir.CellNew, ir.Closure)) for n in walk(main.body)))

    def test_nested_yield_does_not_make_parent_a_generator(self):
        module = self.check('''
from typing import Iterator
def outer() -> Callable[[], Iterator[i32]]:
    def gen() -> Iterator[i32]:
        yield 1
    return gen
''')
        self.assertFalse(next(f for f in module.funcs if f.name == "outer").machine)
        self.assertTrue(next(f for f in module.funcs if f.name.startswith("closure.")).machine)


if __name__ == "__main__":
    unittest.main()
