"""Owned emitter foundations: exact WAT parity, also generated inside Wasm."""
from __future__ import annotations

import json
import os
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bootstrap.build_emitter import bundle_source
from bootstrap.build_bodies import check
from bootstrap.body_bridge import to_module, IRBridge, compile_source
from bootstrap.declarations import DeclType
from bootstrap.emit_support import EmitLiterals, EmitTypes, EmitUnicode, emit_mangle, emit_unicode_flags
from snekc import driver, runner, unicode
from snekc.emit_wat import Emitter, mangle
from snekc.ir import Module
from snekc.wasm import encode


PROBE = '''
def emitter_probe() -> None:
    request = utf8(read_bytes())
    if request == "types":
        types = EmitTypes()
        for ty in emitter_probe_types():
            if ty.kind != "__Buf": print(emit_mangle(ty))
            print(types.valtype(ty))
            print(types.storage(ty))
        print("\\n".join(types.collection_types()))
        print("\\n".join(types.closure_types()))
    elif request.startswith("class\\n"):
        decl = Declarations(declaration_helpers(), declaration_libraries())
        decl.run(parse(request[6:]), parse(declaration_builtin_source()))
        types = EmitTypes()
        print("\\n".join(types.class_types(decl)))
        print(types.vtable_globals(decl))
    elif request.startswith("literal\\n"):
        pool = EmitLiterals()
        print(pool.expand(request[8:]))
        print(pool.functions())
    elif request.startswith("unicode\\n"):
        parts = request.split("\\n", 5)
        kinds: list[i32] = []
        for word in parts[2].split(): kinds.append(i32(word))
        tables = EmitUnicode()
        tables.build([parts[5]], i32(parts[1]), kinds, parts[3] == "True", parts[4] == "True", [])
        print(tables.wat())
    else:
        raise ValueError("unknown emitter probe")
emitter_probe()
'''


def type_fixtures():
    types = [DeclType(k) for k in ("i32", "i64", "f32", "f64", "bool", "str", "__Buf", "TracebackType")]
    string = DeclType("str")
    mapping = DeclType("dict").add(string).add(DeclType("list").add(string))
    cell = DeclType("Cell").add(DeclType("optional").add(string))
    return types + [mapping, DeclType("view", "items").add(mapping), DeclType("tuple"),
                    DeclType("tuple").add(mapping).add(cell), DeclType("Env"), DeclType("Env", "layout"),
                    DeclType("Env", "layout").add(cell), DeclType("Callable").add(mapping).add(DeclType("None")),
                    DeclType("Iterator").add(string), DeclType("Coroutine").add(DeclType("None")),
                    DeclType("Stream", "TextInput"), DeclType("Stream", "BinaryOutput")]


def type_expression(ty):
    return f"DeclType({ty.kind!r}, {ty.name!r})" + ''.join(f".add({type_expression(arg)})" for arg in ty.args)


class EmitterSupportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="snek-emitter-support-")
        cls.directory = Path(cls.temporary.name)
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.binary = cls.directory / "support.wasm"
        # The actual rebuilt checker checks this new emitter source. The
        # Python bridge is still allowed to build the probe executable, but
        # never to generate any of the output being compared by these tests.
        checker = ROOT / "bootstrap/build/bodies.wasm"
        if not checker.exists():
            raise RuntimeError("build bootstrap/bootstrap_checker.py first")
        source = bundle_source() + '\ndef emitter_probe_types() -> list[DeclType]:\n    return ['
        source += ', '.join(type_expression(ty) for ty in type_fixtures()) + ']\n' + PROBE
        cls.binary.write_bytes(encode(compile_source(source, checker)))
        cls.binaries = [cls.binary]
        if os.environ.get("SNEK_WASM_OPT"):
            optimized = cls.directory / "support.opt.wasm"
            result = subprocess.run([*driver.find_wasm_opt(), str(cls.binary), "-o", str(optimized),
                                            *driver.WASM_FEATURES, "-O3", "--closed-world"],
                                           capture_output=True, text=True)
            if result.returncode: raise RuntimeError(result.stderr)
            cls.binaries.append(optimized)

    def generated(self, request, expected):
        for binary in self.binaries:
            with self.subTest(binary=binary.name), \
                 patch("snekc.emit_wat.emit_module", side_effect=AssertionError("emitter fallback")), \
                 patch("snekc.unicode.build", side_effect=AssertionError("Unicode fallback")), \
                 patch("unicodedata.category", side_effect=AssertionError("host Unicode fallback")):
                result = runner.run_binary(binary, stdin=request.encode())
            self.assertEqual((result.code, result.stderr), (0, b""))
            self.assertEqual(result.stdout, expected.encode())

    def test_unicode_all_properties(self):
        # In particular, Unicode Lt is not the same as str.istitle().
        for cp in range(0x110000):
            self.assertEqual(emit_unicode_flags(cp, 4095), unicode.flags_of(cp), hex(cp))

    def test_unicode_tables(self):
        rng = random.Random(813)
        text = "AßİΣǅǲᾈKﬃ１２٣\u0345\u200d\U00010400🦊"
        text += "".join(chr(c) for c in rng.sample(range(0xE000, 0x110000), 300))
        for literals, mask, cases, decimal, dynamic in (
            ("", 0, [], False, False), (text, 4095, [3, 2, 1, 0], True, False),
            ("Σ", 65, [1], False, False), ("٣", 1, [], True, False),
            ("", 4095, [0, 1, 2, 3], True, True),
        ):
            with self.subTest(mask=mask, cases=cases, dynamic=dynamic):
                use = unicode.UnicodeUse(flags=mask, cases=set(cases), decimal=decimal, dynamic=dynamic)
                reference = Emitter(Module([], [], "main", use))
                reference.lit(literals)
                expected = reference.unicode_tables()
                owned = EmitUnicode()
                owned.build([literals], mask, cases, decimal, dynamic, [])
                self.assertEqual(owned.wat(), expected)
                request = f"unicode\n{mask}\n{' '.join(map(str, cases))}\n{decimal}\n{dynamic}\n{literals}"
                self.generated(request, expected + "\n")

    def test_literal_pool(self):
        texts = ['(str.lit "") (str.lit "repeat") (str.lit "repeat")',
                 '(str.lit "🐍\\n\\\" escaped")\n(str.lit "\x00\t\nα")',
                 '(str.lit "unclosed) then (str.lit "bad"no) then (str.lit "ok")']
        texts += [(ROOT / "snekc/prelude.wat").read_text()]
        rng = random.Random(827)
        texts.append('\n'.join('(str.lit "' + ''.join(rng.choices('ab🐍\\"\n\r\t()', k=rng.randrange(40)))
                               + rng.choice(['")', '"no)', '']) for _ in range(1000)))
        for text in texts:
            reference = Emitter(Module([], [], "main"))
            expected = reference.expand_lits(text) + "\n" + reference.literal_funcs() + "\n"
            owned = EmitLiterals()
            self.assertEqual(owned.expand(text) + "\n" + owned.functions() + "\n", expected)
            self.generated("literal\n" + text, expected)

    def test_class_layouts(self):
        for name in ("inheritance", "closures_methods", "context_managers", "suspended_methods"):
            source = (ROOT / f"examples/{name}.py").read_text()
            checked = check(source)
            reference = Emitter(to_module(json.loads(checked.tree().json())))
            expected = "\n".join(reference.class_types()) + "\n" + reference.vtable_globals() + "\n"
            owned = EmitTypes()
            self.assertEqual("\n".join(owned.class_types(checked.decl)) + "\n" + owned.vtable_globals(checked.decl) + "\n", expected)
            self.generated("class\n" + source, expected)

    def test_type_registration(self):
        # Nested collection closure must register the element types too, with
        # the same monomorphization names and nullable-storage ABI.
        bridge = IRBridge()
        reference = Emitter(Module([], [], "main"))
        owned = EmitTypes()
        expected = []
        for ty in type_fixtures():
            host = bridge.ty(json.loads(ty.tree().json()))
            if ty.kind != "__Buf":  # Reference heap() handles this internal type directly.
                self.assertEqual(emit_mangle(ty), mangle(host))
                expected.append(mangle(host))
            self.assertEqual(owned.valtype(ty), reference.valtype(host))
            self.assertEqual(owned.storage(ty), reference.storage(host))
            expected.extend([reference.valtype(host), reference.storage(host)])
        self.assertEqual(owned.collection_types(), reference.collection_types())
        self.assertEqual(owned.closure_types(), reference.closure_types_wat())
        expected.extend(reference.collection_types())
        expected.extend(reference.closure_types_wat())
        self.generated("types", "\n".join(expected) + "\n")


if __name__ == "__main__":
    unittest.main(verbosity=2)
