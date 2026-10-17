"""Shared runtime metadata and independent import-rendering parity."""
import itertools
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bootstrap.emit_host import emit_component_imports, emit_host_core_imports
from snekc import component, runtime_abi, stdlib_host, wasi_bindings
from snekc.check import check_module
from snekc.frontend import parse_source


class RuntimeABITests(unittest.TestCase):
    def test_module_is_supported_snek(self):
        source = (ROOT / "snekc/runtime_abi.py").read_text()
        check_module(parse_source(source, frontend="snek"), frontend="snek")

    def test_all_feature_combinations_have_identical_imports(self):
        features = sorted(wasi_bindings.IMPORTS)
        for size in range(len(features) + 1):
            for chosen in itertools.combinations(features, size):
                with self.subTest(features=chosen):
                    self.assertEqual(emit_host_core_imports(list(chosen)), stdlib_host.core_imports(chosen))
                    for stdin in (False, True):
                        self.assertEqual(emit_component_imports(stdin, list(chosen)),
                                         component.core_imports(stdin=stdin, features=chosen))

    def test_contract_tables_are_fresh_and_do_not_alias(self):
        factories = (runtime_abi.abi_component_base_imports,
                     runtime_abi.abi_component_input_imports,
                     runtime_abi.abi_component_library_imports,
                     runtime_abi.abi_component_arena_imports,
                     runtime_abi.abi_library_operations)
        for factory in factories:
            with self.subTest(factory=factory.__name__):
                a, b = factory(), factory()
                self.assertIsNot(a, b)
                a.clear()
                self.assertTrue(b)
        a, b = runtime_abi.abi_component_library_imports(), runtime_abi.abi_component_library_imports()
        a["random"].clear()
        self.assertTrue(b["random"])

    def test_operation_features_and_binding_names_are_complete(self):
        self.assertEqual(set(runtime_abi.abi_library_operations().values()), set(wasi_bindings.IMPORTS))
        names = list(component.IMPORTS) + list(component.INPUT_IMPORTS) + list(wasi_bindings.ARENA_IMPORTS)
        for bindings in wasi_bindings.IMPORTS.values():
            names.extend(bindings)
        self.assertEqual(len(names), len(set(names)))


if __name__ == "__main__":
    unittest.main()
