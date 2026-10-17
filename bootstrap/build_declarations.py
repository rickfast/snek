"""Link and build the standalone Snek declaration pass (not a full checker).

This is a fixed bootstrap bundle, not a general Python module loader. It joins
the parser, declaration pass, and the encoder's exact numeric helpers. Built-in
source, reserved helper names and library exports are build-time data inputs.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bootstrap.declarations import Declarations
from bootstrap.parser import parse
from bootstrap.linker import NUMERIC_HELPERS, module_source, parser_source
from snekc.builtins import BUILTINS_SOURCE, STR_METHODS_SOURCE
from snekc.formatting import FORMAT_SOURCE
from snekc.stdlib import MODULE_EXPORTS


def configuration() -> tuple[list[str], dict[str, list[str]]]:
    helpers = [node.get("name").text for node in parse(STR_METHODS_SOURCE + FORMAT_SOURCE).get("body").items]
    return helpers, {name: sorted(exports) for name, exports in MODULE_EXPORTS.items()}


def declare(source: str) -> Declarations:
    result = Declarations(*configuration())
    result.run(parse(source), parse(BUILTINS_SOURCE))
    return result


def numeric_helpers() -> str:
    return module_source("encode", select=NUMERIC_HELPERS, rename={"digit": "numeric_digit"})


def bundle_source(*, entry: bool = True) -> str:
    parser = parser_source(entry=False)
    declarations = module_source("declarations")
    helpers, libraries = configuration()
    data = f'''
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


'''
    return parser + "\n" + numeric_helpers() + "\n" + declarations + data + ('\nif __name__ == "__main__":\n    declaration_command()\n' if entry else "")


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
