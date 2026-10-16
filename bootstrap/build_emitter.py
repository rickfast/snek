"""Link the independent Snek compiler from fixed source and runtime data.

This host-side linker is a seed preparation tool, not part of subsequent
compilations. Stage 1/2/3 consume the exact same linked source bytes.
"""
from pathlib import Path
import io
import re
import tokenize

from bootstrap.build_bodies import bundle_source as body_bundle
from bootstrap.build_declarations import remove_once


def bundle_source() -> str:
    support = Path(__file__).with_name("emit_support.py").read_text()
    for line in ("from __future__ import annotations\n", "from snek import i32\n",
                 "from bootstrap.declarations import DeclType, Declarations\n"):
        support = remove_once(support, line)
    program = Path(__file__).with_name("emit_program.py").read_text()
    for line in ("from __future__ import annotations\n", "from snek import i32\n",
                 "from bootstrap.parser import Node, missing\n",
                 "from bootstrap.declarations import DeclType, DeclParam, Declarations\n",
                 "from bootstrap.bodies import BodyExpr, BodyStmt, BodyFunction, BodyChecker, inner_type\n",
                 "from bootstrap.emit_support import EmitTypes, EmitLiterals, EmitUnicode, emit_mangle, emit_utf8\n"):
        program = remove_once(program, line)
    runtime = Path(__file__).with_name("emit_runtime.py").read_text()
    for line in ("from __future__ import annotations\n", "from snek import i32\n",
                 "from bootstrap.declarations import DeclType\n", "from bootstrap.bodies import inner_type\n",
                 "from bootstrap.emit_program import SourceEmitter\n"):
        runtime = remove_once(runtime, line)
    host = Path(__file__).with_name("emit_host.py").read_text()
    for line in ("from __future__ import annotations\n", "from snek import i32\n",
                 "from bootstrap.declarations import DeclType\n", "from bootstrap.emit_program import SourceEmitter\n",
                 "from bootstrap.emit_data import emit_memory_source\n"):
        host = remove_once(host, line)
    module = Path(__file__).with_name("emit_module.py").read_text()
    module = "\n".join(line for line in module.splitlines() if not line.startswith("from ")) + "\n"
    suspension = ""
    for name in ("machine_lower.py", "machine_emit.py", "emit_suspension.py"):
        part = Path(__file__).with_name(name).read_text()
        suspension += "\n" + "\n".join(line for line in part.splitlines() if not line.startswith("from ")) + "\n"
    root = Path(__file__).resolve().parents[1]
    data = "\ndef emit_memory_source() -> str:\n    return " + repr((root / "snekc/abi_memory.wat").read_text()) + "\n"
    data += "\ndef emit_prelude_source() -> str:\n    return " + repr((root / "snekc/prelude.wat").read_text()) + "\n"
    data += "\ndef emit_component_source() -> str:\n    return " + repr((root / "snekc/component_runtime.wat").read_text()) + "\n"
    data += "\ndef emit_input_source() -> str:\n    return " + repr((root / "snekc/component_input.wat").read_text()) + "\n"
    return body_bundle(entry=False) + "\n" + support + "\n" + program + "\n" + runtime + "\n" + host + suspension + "\n" + module + data


def encoder_source() -> str:
    """Namespace the fixed encoder source without rewriting string data."""
    source = Path(__file__).with_name("encode.py").read_text()
    source = remove_once(source, "from __future__ import annotations\n")
    source = remove_once(source, "from snek import i32, i64, read_bytes, write_bytes\n")
    source = remove_once(source, 'if __name__ == "__main__":\n    write_bytes(encode(read_bytes()))\n')
    names = set(re.findall(r"^(?:def|class) ([A-Za-z_][A-Za-z_0-9]*)", source, re.M))
    tokens = []
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.NAME and token.string in names:
            token = token._replace(string="wasm_" + token.string)
        tokens.append(token)
    return tokenize.untokenize(tokens)


def compiler_source(*, binary: bool = True) -> str:
    source = bundle_source()
    source = source.replace("from snek import i32, i64, read_bytes\n", "from snek import i32, i64, read_bytes, sys\n", 1)
    source = source.replace("from snek import i32, i64, read_bytes, sys\n", "from snek import i32, i64, read_bytes, write_bytes, sys\n", 1)
    source += "\n" + encoder_source()
    source += '''
def emitter_command() -> None:
    component = False
    text = False
    encoding = False
    for argument in sys.argv[1:]:
        if argument == "--component": component = True
        elif argument == "--wat": text = True
        elif argument == "--encode": encoding = True
        else: raise ValueError("unknown compiler argument: " + argument)
    data = read_bytes()
    if encoding:
        write_bytes(wasm_encode(data))
        return
    decl = Declarations(declaration_helpers(), declaration_libraries())
    decl.run(parse(utf8(data)), parse(declaration_builtin_source()))
    checked = BodyChecker(decl, body_runtime_tree(), body_runtime_intrinsics())
    checked.run()
    wat = emit_source_module(checked, emit_prelude_source(), component)
'''
    source += '    if text: print(wat, end="")\n    else: write_bytes(wasm_encode(emit_utf8(wat)))\n' if binary else '    print(wat, end="")\n'
    return source + "emitter_command()\n"


def probe_source() -> str:
    return bundle_source() + '''
def emitter_command() -> None:
    decl = Declarations(declaration_helpers(), declaration_libraries())
    decl.run(parse(utf8(read_bytes())), parse(declaration_builtin_source()))
    checked = BodyChecker(decl, body_runtime_tree(), body_runtime_intrinsics())
    checked.run()
    emitter = SourceEmitter(checked)
    functions: list[Node] = []
    for f in checked.functions:
        if not f.machine:
            functions.append(Node("EmittedFunction").put("name", string(f.name)).put("wat", string(emitter.function(f))))
    functions.append(Node("EmittedFunction").put("name", string("helpers")).put("wat", string(emit_collection_helpers(emitter))))
    for name, expr in emitter.keyed.items():
        functions.append(Node("EmittedFunction").put("name", string(name)).put("wat", string(emit_keyed_builtin(emitter, name, expr.args[0].ty.args[0], expr.args[1].ty, expr.text, expr.ty))))
    functions.append(Node("EmittedFunction").put("name", string("bytes")).put("wat", string(emit_byte_io(emitter))))
    print(sequence(functions).json())
emitter_command()
'''
