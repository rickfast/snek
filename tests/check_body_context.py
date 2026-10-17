"""Checked functions retain emission IR, not temporary checker contexts."""
import gc
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import weakref

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bootstrap.bodies import BodyContext, BodyFunction
from bootstrap.build_bodies import check
from bootstrap.declarations import DeclSignature, DeclType
from bootstrap.parser import missing


class BodyContextTests(unittest.TestCase):
    def test_context_owns_function_but_emission_ir_has_no_context_fields(self):
        context = BodyContext("sample", DeclSignature())
        context.declare("value", DeclType("i32"), missing())
        context.flow.assigned["value"] = True
        self.assertEqual(context.get("value", missing()).kind, "LocalGet")
        self.assertEqual(context.temp(DeclType("str"), missing()), "b.0")
        function = context.function
        self.assertIs(type(function), BodyFunction)
        self.assertEqual(set(vars(function)), {
            "name", "sig", "locals", "body", "owner", "method_decl", "machine", "closure_type",
        })
        self.assertEqual(set(function.locals), {"value", "b.0"})
        self.assertEqual(function.tree().kind, "FunctionIR")
        self.assertEqual(function.tree().keys, [
            "name", "signature", "locals", "body", "machine", "closure_type", "owner", "method_decl",
        ])

    def test_finished_functions_release_scope_flow_and_closure_contexts(self):
        fixtures = ("closures", "closures_cleanup", "closures_suspended", "keyed_builtins",
                    "generator_cleanup", "async_functions", "inheritance", "context_cleanup", "format_specs")
        original = BodyContext.__init__
        for fixture in fixtures:
            contexts = []

            def observe(context, name, signature):
                original(context, name, signature)
                contexts.append(weakref.ref(context))

            with self.subTest(fixture=fixture), patch.object(BodyContext, "__init__", observe):
                checked = check((ROOT / "examples" / (fixture + ".py")).read_text())
                gc.collect()
                retained = [reference() for reference in contexts if reference() is not None]
                self.assertEqual(retained, [checked.ctx])
                self.assertEqual(checked.ctx.function.name, "")
                self.assertEqual(checked.ctx.types, {})
                self.assertEqual(checked.ctx.pending, [])
                for function in checked.functions:
                    self.assertIs(type(function), BodyFunction)
                    self.assertFalse(hasattr(function, "scope"))
                    self.assertFalse(hasattr(function, "flow"))
                    self.assertFalse(hasattr(function, "pending"))
                self.assertTrue(checked.functions)

    def test_independent_contexts_do_not_share_function_or_checking_state(self):
        left, right = BodyContext("left", DeclSignature()), BodyContext("right", DeclSignature())
        left.declare("value", DeclType("i32"), missing())
        left.flow.assigned["value"] = True
        self.assertEqual(right.types, {})
        self.assertEqual(right.function.locals, {})
        self.assertEqual(right.flow.assigned, {})
        self.assertIsNot(left.function, right.function)


if __name__ == "__main__":
    unittest.main()
