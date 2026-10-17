"""Aggregate checker parity; includes the scalar checker regression tests."""
from __future__ import annotations

import ast
import textwrap
import unittest
from unittest.mock import patch

import check_bodies as base


PROGRAMS = {
    "literal_context": '''from snek import i32, i64
def main() -> None:
    a = [[1], [], [2, 3]]
    wide: list[i64] = [1, 2147483648]
    maybe: list[str | None] = [None, "x"]
    pairs: tuple[i64, str | None] = (1, None)
    d: dict[str, list[i32]] = {"x": [], "y": [1]}
    print(a, wide, maybe, pairs, d)
    print(list((1, 2)), tuple(), d.get("none"), d.get("none", None))
    print(d.setdefault("x", [4]), d.pop("missing", None))
    a[0].extend([5, 6])
    a[0].remove(5)
    a[0].reverse()
    print(a, a[0].pop(0))
    copy = list(a[0])
    copy.clear()
    print(copy, a[0], a == [[1], [], [2, 3]])
    counts: dict[str, i32] = {"a": 1}
    counts.update({"b": 2})
    view = counts.items()
    counts["c"] = 3
    print(list(view), len(view), ("b", 2) in view)
    counts.clear()
    print(len(view))
main()
''',
    "effects": '''from snek import i32
class Box:
    value: i32
    def __init__(self, value: i32) -> None: self.value = value
    def add(self, a: i32, b: i32) -> i32: return self.value + a + b
def tick(n: i32) -> i32:
    print(n, end=";")
    return n
def receiver(box: Box) -> Box:
    print("recv", end=";")
    return box
def array(xs: list[i32]) -> list[i32]:
    print("array", end=";")
    return xs
def replace(xs: list[i32]) -> i32:
    xs[0] = 100
    return 2
def main() -> None:
    b = Box(1)
    receiver(b).value = tick(7)
    print(b.value)
    print(receiver(b).add(b=tick(3), a=tick(2)))
    xs = [10]
    array(xs)[tick(0)] = tick(4)
    print(xs)
    array(xs)[tick(0)] += replace(xs)
    print(xs)
    print(tick(4) in array(xs))
    print(tick(2) * array(xs))
    d = {tick(1): tick(2), tick(3): tick(4)}
    print(d)
    d[tick(1)] = tick(9)
    print(d)
    pair = (1, 2)
    pair = (pair[1], pair[0])
    x, y = pair
    print(x, y)
main()
''',
    "constructors": '''from snek import i32
from typing import Final
class Base:
    a: Final[i32]
    values: list[i32]
    def __init__(self, a: i32) -> None:
        local = a + 1
        self.a = local
        self.values = []
    def get(self, add: i32 = 2) -> i32:
        return self.a + add
class Middle(Base):
    b: i32
    def __init__(self, a: i32, b: i32 = 3) -> None:
        local = a + b
        super().__init__(a=local)
        self.b = b
    def get(self, add: i32 = 2) -> i32:
        return super().get(add) + self.b
class Leaf(Middle):
    c: str = "leaf"
class Empty: pass
def read(value: Base) -> i32: return value.get()
def main() -> None:
    a = Leaf(4)
    b = Leaf(5, b=8)
    a.values.append(9)
    print(read(a), read(b), a.values, b.values, a.c)
    obj: Base | None = a
    if isinstance(obj, Leaf):
        print(obj.c)
    print(isinstance(obj, Middle), a is b, a is not b)
    choice: Base = a if True else Base(2)
    print(choice.get(add=1))
    print(Empty() == Empty(), bool(Empty()))
main()
''',
    "protocols": '''from snek import i32
class Value:
    n: i32
    def __init__(self, n: i32) -> None: self.n = n
    def __eq__(self, other: Value | None) -> bool:
        if other is None: return False
        return self.n == other.n
    def __len__(self) -> i32: return self.n
    def __str__(self) -> str: return "v:" + str(self.n)
def main() -> None:
    a = Value(2)
    b = Value(2)
    print(a == b, a != b, len(a), bool(a), str(a))
    values = [a, b]
    print(Value(2) in values, values.count(a), values == [a, a])
    x: Value | None = None
    y: Value | None = b
    print(x == y, str(x), str(y), x == None, None == x, a == None)
main()
''',
    "loops": '''from snek import i32
def visit(d: dict[str, i32]) -> i32:
    for key in d:
        for value in d.values():
            return value
    return -1
def main() -> None:
    d = {"a": 1, "b": 2}
    print(visit(d))
    for key in d:
        break
    d["c"] = 3
    for key, value in d.items():
        if key == "b": continue
        print(key, value)
    for key in d:
        d[key] += 10
    print(d)
    xs = [1]
    for value in xs:
        print(value)
        if value < 3: xs.append(value + 1)
    for value in (4, 5): print(value)
main()
''',
}

PROGRAMS = {name: "from __future__ import annotations\n" + source for name, source in PROGRAMS.items()}


def local_program(source):
    """Keep declarations at module scope and exercise statements as locals."""
    declarations, statements = [], []
    for node in ast.parse(source).body:
        segment = ast.get_source_segment(source, node)
        if isinstance(node, (ast.Import, ast.ImportFrom, ast.ClassDef, ast.FunctionDef)):
            declarations.append(segment)
        else:
            statements.append(segment)
    body = textwrap.indent("\n".join(statements) or "pass", "    ")
    return "from __future__ import annotations\n" + "\n".join(declarations) + "\ndef main() -> None:\n" + body + "\nmain()\n"


ERRORS = [
    "x = []", "x = {}", "x = [None]", "x = (None,)",
    "x = [1, 2.0]", "x = {1: 2, 3: 4.5}", "x = {[1]: 2}",
    "from snek import i32\nx: list[i32] = [1]\nx.append('x')",
    "from snek import i32\nx: list[i32] = [1]\nx[0] = 'x'",
    "from snek import i32\nx: list[i32] | None = None\nprint(x[0])",
    "from snek import i32\nx: list[i32] | None = None\nx.append(2)",
    "x = (1, 'a')\ni = 0\nprint(x[i])", "x, y = (1,)",
    "x = (1, 2)\nx[0] += 1", "x = (1, 2)\nx[0] = 1",
    "def f() -> None:\n    for item in [1]: pass\n    print(item)",
    "from snek import i32\nclass C:\n    x: i32\nC()",
    "from snek import i32\nclass C:\n    x: i32\n    def __init__(self) -> None:\n        print(self.x)\n        self.x = 1",
    "from snek import i32\nclass C:\n    x: i32\n    def __init__(self, flag: bool) -> None:\n        if flag: self.x = 1",
    "from snek import i32\nclass C:\n    x: i32\n    def __init__(self) -> None: self.x = 'x'",
    "class C:\n    def __init__(self) -> None: self.undeclared = 1",
    "from snek import i32\nfrom typing import Final\nclass C:\n    x: Final[i32] = 1\nc = C()\nc.x = 2",
    "from snek import i32\nfrom typing import Final\nclass C:\n    x: Final[i32] = 1\nc = C()\nc.x += 2",
    "class C:\n    def f(self) -> None: self = C()",
    "from snek import i32\nclass C:\n    def f(self) -> i32: pass",
    "from snek import i32\nclass C:\n    x: i32 = 1\nc: C | None = None\nprint(c.x)",
    "class C: pass\nx: list[C] = [C()]\nprint(x)",
    "class C: pass\nprint(repr(C()))",
    "from snek import i32\nclass A:\n    x: i32\n    def __init__(self) -> None: self.x = 1\nclass B(A):\n    def __init__(self) -> None:\n        self.x = 2\n        print(self.x)\n        super().__init__()",
]


class AggregateCases:
    def test_aggregate_fixture(self):
        self.compare((base.ROOT / "examples/aggregate_checker.py").read_text())
        self.compare((base.ROOT / "examples/lists.py").read_text())

    def test_aggregate_behavior(self):
        for name, source in PROGRAMS.items():
            with self.subTest(name=name):
                self.compare(source)

    def test_aggregate_errors(self):
        for source in ERRORS:
            source = local_program(source)
            with self.subTest(source=source):
                with self.assertRaises(ValueError) as error:
                    base.check(source)
                with self.assertRaises(base.CompileError):
                    base.driver.compile_to_wat(source)
                result = base.runner.run_binary(self.wasm, stdin=source.encode())
                self.assertNotEqual(result.code, 0)
                self.assertEqual(result.stdout, b"")
                self.assertIn(str(error.exception), result.stderr.decode())

    def test_aggregate_runtime_errors(self):
        for source in [
            "print([1][4])", "print({'a': 1}['b'])", "print([1].pop(3))",
            "x = [1]\nx.remove(2)", "print([1].index(2))",
            "x = {'a': 1}\ndel x['b']", "x = {'a': 1}\nprint(x.pop('b'))",
            "x = [1]\nprint(x[::0])",
            "d = {'a': 1}\nfor key in d:\n    d['b'] = 2",
            "from snek import i32\nclass C:\n    def __len__(self) -> i32: return -1\nprint(len(C()))",
        ]:
            source = local_program(source)
            with self.subTest(source=source):
                self.compare(source)

    def test_aggregate_without_python_checking(self):
        source = (base.ROOT / "examples/aggregate_checker.py").read_text()
        expected = base.compile_source(source)
        with patch("ast.parse", side_effect=AssertionError("Python parser fallback")), \
             patch("snekc.frontend.to_ast", side_effect=AssertionError("source AST adapter fallback")), \
             patch.object(base.Checker, "run", side_effect=AssertionError("Python checker fallback")), \
             patch.object(base.Checker, "declare_module", side_effect=AssertionError("declaration fallback")):
            self.assertEqual(base.compile_source(source), expected)
            self.assertEqual(base.compile_source(source, self.wasm), expected)

    def test_owned_class_layouts_and_methods(self):
        source = (base.ROOT / "examples/aggregate_checker.py").read_text()
        data = base.check_source(source, self.wasm)
        self.assertEqual(data["subset"], "language-v7")
        module = base.to_module(data)
        original = base.Checker(base.parse_source(source)).run()
        expected = {cls.name: cls for cls in original.classes}
        for cls in module.classes:
            other = expected[cls.name]
            self.assertEqual([(f.name, str(f.ty), f.final, f.index, f.owner.name) for f in cls.fields.values()],
                             [(f.name, str(f.ty), f.final, f.index, f.owner.name) for f in other.fields.values()])
            self.assertEqual([(m.name, m.decl.name, m.owner.name, m.slot) for m in cls.methods.values()],
                             [(m.name, m.decl.name, m.owner.name, m.slot) for m in other.methods.values()])
        functions = {fn.name: fn for fn in module.funcs}
        self.assertEqual(functions["Tagged.label"].sig_type, "Node.label.sig")
        self.assertEqual(functions["Tagged.label"].self_cast.name, "Tagged")
        self.assertIn("self", functions["Tagged.label"].nonnull_locals)

    def test_optimized_aggregate_checker(self):
        if not base.os.environ.get("SNEK_WASM_OPT"):
            self.skipTest("set SNEK_WASM_OPT to include optimized checker")
        optimized = self.directory / "aggregates.opt.wasm"
        base.driver.build(self.path, optimized, optimize=True, frontend="snek")
        for source in [*PROGRAMS.values(), (base.ROOT / "examples/aggregate_checker.py").read_text()]:
            self.assertEqual(base.check_source(source, optimized), base.check_source(source))

    def test_aggregate_component_core(self):
        source = (base.ROOT / "examples/aggregate_checker.py").read_text()
        wat = base.compile_source(source, self.wasm, component=True)
        binary = self.directory / "aggregates.component-core.wasm"
        binary.write_bytes(base.encode(wat))
        self.assertNotIn('"wasi_snapshot_preview1"', wat)
        if base.shutil.which("wasm-tools"):
            result = base.subprocess.run(["wasm-tools", "validate", str(binary)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)


CASES = (*base.CASES, AggregateCases)
AggregateTests = base.checker_variant("AggregateTests", CASES)


if __name__ == "__main__":
    unittest.main(verbosity=2)
