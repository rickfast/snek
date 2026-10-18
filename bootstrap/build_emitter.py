"""Link the independent Snek compiler from fixed source and runtime data.

This host-side linker is a seed preparation tool, not part of subsequent
compilations. Stage 1/2/3 consume the exact same linked source bytes.
"""
from pathlib import Path

from bootstrap.build_bodies import bundle_source as body_bundle
from bootstrap.linker import module_source, statements


COMPILER_STEPS = (
    ("read", "data = read_bytes()"),
    ("decode_source", "source = utf8(data)"),
    ("declaration_setup", "decl = Declarations(declaration_helpers(), declaration_libraries())"),
    ("parse_source", "module = parse(source)"),
    ("parse_builtins", "builtins = parse(declaration_builtin_source())"),
    ("declarations", "decl.run(module, builtins)"),
    ("runtime_index", "runtime = body_runtime_tree()"),
    ("body_setup", "checked = BodyChecker(decl, runtime, body_runtime_intrinsics())"),
    ("check_bodies", "checked.run()"),
    ("emit_wat", "wat = emit_source_module(checked, emit_prelude_source(), component, exports, entry)"),
)
ENCODE_STEPS = (
    ("encode_wat_utf8", "wat_bytes = emit_utf8(wat)"),
    ("encode_wasm", "output = wasm_encode(wat_bytes)"),
    ("write", "write_bytes(output)"),
)


def bundle_source() -> str:
    parts = [module_source(name) for name in (
        "modules", "library", "runtime_abi", "emit_support", "emit_program", "emit_runtime", "emit_host",
        "machine_lower", "machine_emit", "tasks", "emit_suspension", "emit_module")]
    root = Path(__file__).resolve().parents[1]
    data = "\ndef emit_memory_source() -> str:\n    return " + repr((root / "snekc/abi_memory.wat").read_text()) + "\n"
    data += "\ndef emit_prelude_source() -> str:\n    return " + repr((root / "snekc/prelude.wat").read_text()) + "\n"
    data += "\ndef emit_component_source() -> str:\n    return " + repr((root / "snekc/component_runtime.wat").read_text()) + "\n"
    data += "\ndef emit_input_source() -> str:\n    return " + repr((root / "snekc/component_input.wat").read_text()) + "\n"
    data += "\ndef emit_library_runtime() -> str:\n    return " + repr((root / "snekc/library_runtime.wat").read_text()) + "\n"
    data += "\ndef emit_library_javascript() -> str:\n    return " + repr((root / "snekc/library.js").read_text()) + "\n"
    return body_bundle(entry=False) + "\n" + "\n".join(parts) + data


def encoder_source() -> str:
    """Namespace the fixed encoder source without rewriting string data."""
    source = module_source("encode")
    names = [statement.words[1] for statement in statements(source)]
    return module_source("encode", rename={name: "wasm_" + name for name in names})


def compiler_entry(*, binary: bool = True) -> str:
    source = '''
def emitter_command() -> None:
    component = False
    text = False
    encoding = False
    project = False
    library = False
    exports: list[str] = []
    entry = ""
    for argument in sys.argv[1:]:
        if argument == "--component": component = True
        elif argument == "--wat": text = True
        elif argument == "--encode": encoding = True
        elif argument == "--project": project = True
        elif argument == "--library": library = True
        elif argument.startswith("--export="): exports.append(argument[9:])
        else: raise ValueError("unknown compiler argument: " + argument)
    if library and (component or encoding): raise ValueError("library output cannot be combined with component/encoder mode")
    if library and len(exports) == 0: raise ValueError("--library requires at least one --export FUNCTION")
    if not library and len(exports) != 0: raise ValueError("--export requires --library")
    data = read_bytes()
    if encoding:
        write_bytes(wasm_encode(data))
        return
'''
    for name, statement in COMPILER_STEPS[1:]:
        if name == "parse_source":
            source += '''    if project:
        entry = source.split("\\n")[1]
        try:
            module = module_project(source, library)
        except ValueError as error:
            if str(error).startswith("SNEK-MODULE-NEEDED "):
                print(str(error))
                return
            raise
    elif library:
        entry = "library"
        module = module_library(source)
    else:
        module = parse(source)
'''
        else:
            source += "    " + statement + "\n"
    if binary:
        source += '    if text:\n        print(wat, end="")\n        return\n'
        source += "".join("    " + statement + "\n" for _, statement in ENCODE_STEPS[:-1])
        source += '''    if library:
        metadata = wat.split("\\n;; SNEK-BINDINGS ")[1].split("\\n")[0]
        output = emit_utf8("SNEK-LIBRARY-1\\n" + metadata + "\\n") + output
    write_bytes(output)
'''
    else:
        source += '    print(wat, end="")\n'
    return source + "emitter_command()\n"


def compiler_source(*, binary: bool = True) -> str:
    return bundle_source() + "\n" + encoder_source() + compiler_entry(binary=binary)


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
