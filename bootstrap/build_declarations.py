"""Link and build the standalone Snek declaration pass (not a full checker).

This is a fixed bootstrap bundle, not a general Python module loader. It joins
the parser, declaration pass, and the encoder's exact numeric helpers. Built-in
source, reserved helper names and library exports are build-time data inputs.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bootstrap.declarations import Declarations
from bootstrap.parser import parse
from snekc.builtins import BUILTINS_SOURCE, STR_METHODS_TEMPLATE
from snekc.formatting import FORMAT_SOURCE
from snekc.stdlib import MODULE_EXPORTS


def configuration() -> tuple[list[str], dict[str, list[str]]]:
    helpers = re.findall(r"^def ([A-Za-z_][A-Za-z_0-9]*)\(", STR_METHODS_TEMPLATE + FORMAT_SOURCE, re.M)
    return helpers, {name: sorted(exports) for name, exports in MODULE_EXPORTS.items()}


def declare(source: str) -> Declarations:
    result = Declarations(*configuration())
    result.run(parse(source), parse(BUILTINS_SOURCE))
    return result


def remove_once(source: str, text: str) -> str:
    if source.count(text) != 1:
        raise ValueError(f"bootstrap linker expected exactly one {text!r}")
    return source.replace(text, "", 1)


def numeric_helpers() -> str:
    source = (ROOT / "bootstrap/encode.py").read_text()
    names = ["need", "digit", "big_trim", "big_mul", "big_shift", "big_cmp", "big_sub",
             "big_bits", "big_parse", "rounded_ratio", "float_bytes"]
    parts = []
    for name in names:
        # These are undecorated top-level functions in a controlled source file.
        # Stop at the next unindented line; no AST parsing/rewrite is involved.
        matches = list(re.finditer(rf"^def {name}\(.*(?:\n(?:[ \t]+[^\n]*|))*", source, re.M))
        if len(matches) != 1:
            raise ValueError(f"bootstrap linker cannot find numeric helper {name}")
        parts.append(matches[0].group())
    # The source parser already owns digit(). Both occurrences here are code,
    # not string data; fail closed if the encoder's helper shape changes.
    result = "\n\n".join(parts)
    for old, new in (("def digit(", "def numeric_digit("), ("d = digit(char)", "d = numeric_digit(char)")):
        if result.count(old) != 1:
            raise ValueError(f"bootstrap numeric helper contract changed: {old}")
        result = result.replace(old, new, 1)
    return result


def bundle_source() -> str:
    parser = (ROOT / "bootstrap/parser.py").read_text()
    parser = remove_once(parser, 'if __name__ == "__main__":\n    print(parse(utf8(read_bytes())).json())\n')
    declarations = (ROOT / "bootstrap/declarations.py").read_text()
    for line in (
        "from __future__ import annotations\n",
        "from snek import i32, i64\n",
        "from bootstrap.parser import Node, fail, missing, parse_expression, scalar, sequence, string\n",
        "from bootstrap.encode import float_bytes\n",
    ):
        declarations = remove_once(declarations, line)
    parser = remove_once(parser, "from snek import i32, read_bytes\n")
    parser = parser.replace("from __future__ import annotations\n",
                            "from __future__ import annotations\nfrom snek import i32, i64, read_bytes\n", 1)
    helpers, libraries = configuration()
    entry = f'''
def declaration_builtin_source() -> str:
    return {BUILTINS_SOURCE!r}


def declaration_helpers() -> list[str]:
    return {helpers!r}


def declaration_libraries() -> dict[str, list[str]]:
    return {libraries!r}


def declaration_command() -> None:
    result = Declarations(declaration_helpers(), declaration_libraries())
    result.run(parse(utf8(read_bytes())), parse(declaration_builtin_source()))
    print(result.tree().json())


if __name__ == "__main__":
    declaration_command()
'''
    return parser + "\n" + numeric_helpers() + "\n" + declarations + entry


def build(directory: Path) -> dict:
    from snekc import driver, runner

    source = bundle_source()
    expected = json.loads(declare(source).tree().json())
    with tempfile.TemporaryDirectory(prefix="snek-declarations-") as temporary:
        work = Path(temporary)
        src, wasm = work / "declarations.bundle.py", work / "declarations.wasm"
        src.write_text(source)
        driver.build(src, wasm, optimize=False, frontend="snek")
        result = runner.run_binary(wasm, stdin=source.encode())
        if result.code or result.stderr:
            raise RuntimeError(f"standalone declaration pass failed: {result.stderr.decode(errors='replace')}")
        if json.loads(result.stdout) != expected:
            raise RuntimeError("Wasm and shim self-declarations differ")
        directory.mkdir(parents=True, exist_ok=True)
        for path in (src, wasm):
            shutil.copyfile(path, directory / path.name)
        return {"scope": "declarations only; function bodies are not checked", "bytes": wasm.stat().st_size,
                "classes": len(expected["classes"]), "functions": len(expected["functions"])}


def main() -> int:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--out", type=Path, default=ROOT / "bootstrap/build")
    args = cli.parse_args()
    print(json.dumps(build(args.out), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
