"""Local projects: namespace isolation, initialization, diagnostics and host safety."""
from contextlib import ExitStack
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from snekc import driver, runner
from snekc.errors import CompileError

COMPONENT = "--component" in sys.argv
if COMPONENT:
    sys.argv.remove("--component")


class ModuleTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="snek-modules-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.entry = self.root / "main.py"
        self.compiler = os.environ.get("SNEK_COMPILER", "python")

    def files(self, files):
        for name, text in files.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")

    def build(self, **kwargs):
        return driver.build(self.entry, self.root / "output.wasm", optimize=False,
                            compiler=self.compiler, component=COMPONENT, **kwargs)

    def compare(self, expected, *, root=None, cpython=True):
        for frontend in (("cpython", "snek") if self.compiler == "python" else (None,)):
            result = runner.run_binary(self.build(module_root=root, frontend=frontend).wasm_path)
            self.assertEqual((result.code, result.stdout, result.stderr), (0, expected, b""))
        if cpython:
            env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(self.root), str(ROOT)]), "PYTHONIOENCODING": "utf-8"}
            result = subprocess.run([sys.executable, str(self.entry)], capture_output=True, env=env, timeout=30)
            self.assertEqual((result.returncode, result.stdout.replace(b"\r\n", b"\n"), result.stderr), (0, expected, b""))

    def test_initialization_aliases_and_collisions(self):
        self.files({
            "main.py": 'print("entry before")\nimport left as a\nprint("entry middle")\nfrom right import value as b\nimport left\nprint(a.value(), b(), left.value())\n',
            "left.py": 'from snek import i32\nprint("left")\ndef value() -> i32:\n    return 20\nif __name__ == "__main__":\n    print("not imported")\n',
            "right.py": 'from snek import i32\nprint("right")\ndef value() -> i32:\n    return 22\n',
        })
        self.compare(b"entry before\nleft\nentry middle\nright\n20 22 20\n")

    def test_packages_relative_imports_and_reexports(self):
        self.files({
            "main.py": 'from pkg import answer\nimport pkg.child\nfrom pkg import child as c\nprint(answer(), pkg.child.answer(), c.answer())\n',
            "pkg/__init__.py": 'print("package")\nfrom .child import answer\n',
            "pkg/child.py": 'from snek import i32\nfrom .leaf import twice\nprint(__name__)\ndef answer() -> i32:\n    return twice(21)\n',
            "pkg/leaf.py": 'from snek import i32\ndef twice(n: i32) -> i32:\n    return n * 2\n',
        })
        self.compare(b"package\npkg.child\n42 42 42\n")

    def test_cross_module_classes_and_quoted_annotations(self):
        self.files({
            "main.py": 'from models import Base\nfrom other import Child\ndef take(x: "Base") -> "Base":\n    return x\nprint(take(Child(7)).value())\nprint(type(Child(8)).__name__)\n',
            "models.py": 'from snek import i32\nclass Base:\n    n: i32\n    def __init__(self, n: i32) -> None:\n        self.n = n\n    def value(self) -> i32:\n        return self.n\n',
            "other.py": 'from models import Base\nfrom snek import i32\nclass Child(Base):\n    def value(self) -> i32:\n        return super().value() + 1\n',
        })
        self.compare(b"8\nChild\n")

    def test_closure_and_parameter_shadowing(self):
        self.files({
            "main.py": 'from snek import i32\nfrom typing import Callable\nfrom helpers import value\ndef outer(value: i32) -> Callable[[i32], i32]:\n    def inner(n: i32) -> i32:\n        return value + n\n    return inner\nprint(outer(40)(2), value())\n',
            "helpers.py": 'from snek import i32\ndef value() -> i32:\n    return 9\n',
        })
        self.compare(b"42 9\n")

    def test_comprehension_scopes_preserve_imports_and_initialization_locals(self):
        self.files({
            "main.py": '''from snek import i32
from helpers import value, items
def show() -> None:
    print([value + 1 for value in items()], value())
    print([[value + inner for inner in range(2)] for value in items()])
show()
for x in range(2):
    print([x for x in range(x + 1)], x)
''',
            "helpers.py": '''from snek import i32
def value() -> i32:
    return 9
def items() -> list[i32]:
    return [1, 2]
''',
        })
        self.compare(b"[2, 3] 9\n[[1, 2], [2, 3]]\n[0] 0\n[0, 1] 1\n")

    def test_standard_library_aliases_are_per_module(self):
        self.files({
            "main.py": 'from left import show\nfrom right import count\nshow()\nprint(count())\n',
            "left.py": 'from snek import sys as lib\ndef show() -> None:\n    lib.stdout.write("left\\n")\n',
            "right.py": 'from snek import i32 as Int\nfrom snek import os as lib\ndef count() -> Int:\n    return len(lib.getenv("SNEK_UNGRANTED_TEST", "right"))\n',
        })
        self.compare(b"left\n5\n")

    def test_snek_extension(self):
        self.files({"main.py": "from helper import answer\nprint(answer())\n",
                    "helper.snek": "from snek import i32\ndef answer() -> i32:\n    return 42\n"})
        self.compare(b"42\n", cpython=False)

    def test_explicit_root_for_package_entry(self):
        self.files({"pkg/main.py": "from .helper import answer\nprint(answer())\n",
                    "pkg/__init__.py": "print('package')\n",
                    "pkg/helper.py": "from snek import i32\ndef answer() -> i32:\n    return 42\n"})
        self.entry = self.root / "pkg/main.py"
        self.compare(b"package\n42\n", root=self.root, cpython=False)

    def test_initializing_package_can_import_children_and_siblings(self):
        self.files({"main.py": "from pkg import child\nprint(child.answer())\n",
                    "pkg/__init__.py": "from . import child\n",
                    "pkg/child.py": "from . import sibling\nfrom snek import i32\ndef answer() -> i32:\n    return sibling.answer()\n",
                    "pkg/sibling.py": "from snek import i32\ndef answer() -> i32:\n    return 42\n"})
        self.compare(b"42\n")

    def test_package_submodule_cannot_replace_a_declaration(self):
        self.files({"main.py": "import pkg.child\n", "pkg/__init__.py": "def child() -> None:\n    pass\n",
                    "pkg/child.py": ""})
        with self.assertRaisesRegex(CompileError, "already bound"):
            self.build()

    def test_repository_example(self):
        self.entry = ROOT / "examples/modules/main.py"
        self.compare(b"25.0\nPoint\n")

    def test_initializer_temporaries_do_not_leak(self):
        self.files({"main.py": "import a\nimport b\n",
                    "a.py": "for item in [1, 2]:\n    print(item)\n",
                    "b.py": 'for item in ["a", "b"]:\n    print(item)\n'})
        self.compare(b"1\n2\na\nb\n")

    def test_initializer_lambda_parameter_shadows_loop_variable(self):
        self.files({"main.py": "from snek import i32\nfrom typing import Callable\nimport a\nfor x in [1]:\n    f: Callable[[i32], i32] = lambda x: x + 1\n    print(f(41))\n", "a.py": ""})
        self.compare(b"42\n")

    def test_conditional_module_declarations_are_rejected(self):
        for condition in ("True", '__name__ == "__main__"'):
            with self.subTest(condition=condition):
                self.files({"main.py": "import a\nif " + condition + ":\n    def f() -> None:\n        pass\nf()\n", "a.py": ""})
                with self.assertRaisesRegex(CompileError, "declared directly at module scope"):
                    self.build()

    def test_imported_modules_cannot_be_mutated(self):
        self.files({"main.py": "import a\ndef main() -> None:\n    a.answer = 0\nmain()\n",
                    "a.py": "from snek import i32\ndef answer() -> i32:\n    return 42\n"})
        with self.assertRaisesRegex(CompileError, "cannot be assigned"):
            self.build()

    def test_package_and_module_collision(self):
        self.files({"main.py": "import a\n", "a.py": "", "a/__init__.py": ""})
        with self.assertRaisesRegex(ValueError, "ambiguous module"):
            self.build()

    def test_root_escape_and_relative_escape(self):
        self.files({"main.py": "from ..outside import answer\n"})
        with self.assertRaisesRegex(CompileError, "escapes the package"):
            self.build()
        with tempfile.TemporaryDirectory() as outside:
            external = Path(outside) / "outside.py"
            external.write_text("print(42)\n")
            self.files({"main.py": "import outside\n"})
            (self.root / "outside.py").symlink_to(external)
            with self.assertRaisesRegex(ValueError, "outside --module-root"):
                self.build()

    def test_missing_import(self):
        self.files({"main.py": "import absent\n"})
        with self.assertRaisesRegex(CompileError, "not found"):
            self.build()

    def test_cycles_show_chain(self):
        self.files({"main.py": "import a\n", "a.py": "import b\n", "b.py": "import a\n"})
        with self.assertRaisesRegex(CompileError, "main -> a -> b -> a"):
            self.build()

    def test_ambiguous_sources(self):
        self.files({"main.py": "import a\n", "a.py": "", "a.snek": ""})
        with self.assertRaisesRegex(ValueError, "ambiguous module"):
            self.build()

    def test_wildcards_and_missing_exports(self):
        for statement, error in [("from a import *", "wildcard"), ("from a import nope", "cannot import"),
                                 ("import a\nprint(a.nope)", "no exported name"),
                                 ("import a\nprint(a)", "static namespaces")]:
            with self.subTest(statement=statement):
                self.files({"main.py": statement + "\n", "a.py": ""})
                with self.assertRaisesRegex(CompileError, error): self.build()

    def test_types_do_not_leak_between_modules(self):
        self.files({"main.py": "from snek import i32\nfrom a import answer\nprint(answer())\n",
                    "a.py": "def answer() -> i32:\n    return 42\n"})
        with self.assertRaisesRegex(CompileError, "must be imported in this module"):
            self.build()

    def test_unused_invalid_standard_imports_are_rejected(self):
        self.files({"main.py": "import a\nimport snek.nonexistent\n", "a.py": ""})
        with self.assertRaisesRegex(CompileError, "unsupported standard-library module"):
            self.build()

    def test_imported_error_reports_original_file(self):
        self.files({"main.py": "import a\n", "a.py": "def broken() -> None:\n    print(missing)\n"})
        with self.assertRaises(CompileError) as caught: self.build()
        error = caught.exception
        self.assertEqual(error.filename, str(self.root / "a.py"))
        self.assertEqual(error.lineno, 2)
        self.assertIn("print(missing)", error.format(str(self.entry)))

    def test_imported_syntax_error_reports_original_file(self):
        self.files({"main.py": "import a\n", "a.py": "def broken() -> None\n    pass\n"})
        with self.assertRaises(CompileError) as caught: self.build()
        self.assertEqual(caught.exception.filename, str(self.root / "a.py"))
        self.assertEqual(caught.exception.lineno, 1)
        self.assertIn("syntax error", caught.exception.msg)

    def test_outputs_cannot_overwrite_imported_sources(self):
        self.files({"main.py": "import a\n", "a.py": "print(42)\n"})
        imported = self.root / "a.py"
        with self.assertRaisesRegex(driver.ToolError, "project source"):
            driver.build(self.entry, imported, optimize=False, compiler=self.compiler)
        self.assertEqual(imported.read_text(), "print(42)\n")

    def test_no_host_compiler_in_selfhost_projects(self):
        self.files({"main.py": "from a import answer\nprint(answer())\n",
                    "a.py": "from snek import i32\ndef answer() -> i32:\n    return 42\n"})
        with ExitStack() as guards:
            if self.compiler == "snek":
                for name in ("ast.parse", "bootstrap.parser.parse", "bootstrap.modules.module_project", "snekc.check.Checker.run"):
                    guards.enter_context(patch(name, side_effect=AssertionError("host fallback: " + name)))
            self.compare(b"42\n")


if __name__ == "__main__":
    unittest.main()
