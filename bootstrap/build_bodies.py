"""Build a standalone probe of the production owned body checker."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bootstrap.bodies import BodyChecker
from bootstrap.build_declarations import bundle_source as declaration_bundle, declare
from bootstrap.parser import parse, string
from bootstrap.linker import module_source
from bootstrap.runtime_data import configuration, indexed_runtime, runtime_functions


SMOKE_SOURCE = '''from snek import i32
def fib(n: i32) -> i32:
    if n < 2:
        return n
    return fib(n - 1) + fib(n - 2)

class Sum:
    values: list[i32]
    def __init__(self, values: list[i32]) -> None:
        self.values = values
    def total(self) -> i32:
        ordered = sorted(self.values, reverse=True)
        assert min(ordered) == 0 and max(ordered) == 34
        return sum(ordered)

def parse_number(text: str) -> i32:
    try:
        return i32(text.strip())
    except ValueError:
        return 0

def main() -> None:
    answers = {"answer": Sum([fib(parse_number(" 9 ")), fib(8), parse_number("bad")])}
    print(answers["answer"].total())
main()
'''


def check(source: str) -> BodyChecker:
    runtime, intrinsics, contract = configuration()
    result = BodyChecker(declare(source), indexed_runtime(runtime, contract), intrinsics)
    result.run()
    return result


def bundle_source(*, entry: bool = True) -> str:
    source = declaration_bundle(entry=False)
    source += "\n" + module_source("formats") + "\n" + module_source("scopes")
    bodies = module_source("bodies")
    runtime, intrinsics, contract = configuration()
    helpers = runtime_functions(runtime)
    data = '\ndef body_runtime_tree() -> Node:\n    functions: list[Node] = []\n'
    for name, line, text in helpers:
        data += f'    functions.append(Node("FunctionSource", {text!r}, {line}).put("name", string({name!r})))\n'
    data += '    return Node("Module").put("runtime_abi", string(body_runtime_contract())).put("body", sequence(functions))\n'
    data += f'''
def body_runtime_intrinsics() -> dict[str, list[str]]:
    return {intrinsics!r}

def body_runtime_contract() -> str:
    return {contract!r}
'''
    return source + "\n" + bodies + data + ('''

def body_command() -> None:
    decl = Declarations(declaration_helpers(), declaration_libraries())
    decl.run(parse(utf8(read_bytes())), parse(declaration_builtin_source()))
    runtime = body_runtime_tree()
    checked = BodyChecker(decl, runtime, body_runtime_intrinsics())
    checked.run()
    print(checked.tree().json())


if __name__ == "__main__":
    body_command()
''' if entry else '')


def build(directory: Path) -> dict:
    from snekc import driver, runner
    from bootstrap.body_bridge import to_module
    from snekc.emit_wat import emit_module
    from snekc.wasm import encode

    source = bundle_source()
    expected = json.loads(check(SMOKE_SOURCE).tree().json())
    with tempfile.TemporaryDirectory(prefix="snek-bodies-") as temporary:
        work = Path(temporary)
        src, wasm = work / "bodies.bundle.py", work / "bodies.wasm"
        src.write_text(source)
        driver.build(src, wasm, optimize=False, frontend="snek")
        result = runner.run_binary(wasm, stdin=SMOKE_SOURCE.encode())
        if result.code or result.stderr:
            raise RuntimeError(f"standalone body checker failed: {result.stderr.decode(errors='replace')}")
        actual = json.loads(result.stdout)
        if actual != expected:
            raise RuntimeError("Wasm and shim typed IR differ")
        program = work / "smoke.wasm"
        program.write_bytes(encode(emit_module(to_module(actual))))
        executed = runner.run(program)
        if executed.code or executed.stderr or executed.stdout != "55\n":
            raise RuntimeError(f"checked program failed: {executed}")
        directory.mkdir(parents=True, exist_ok=True)
        for path in (src, wasm):
            shutil.copyfile(path, directory / path.name)
        return {"scope": "self-check-capable body checker including closures and suspension; Python emitter/runtime bridge still required",
                "bytes": wasm.stat().st_size, "smoke_output": executed.stdout.strip()}


def main() -> int:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--out", type=Path, default=ROOT / "bootstrap/build")
    args = cli.parse_args()
    print(json.dumps(build(args.out), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
