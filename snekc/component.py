"""Native WASI 0.3 command component wiring (no preview1 adapter).

The core program's GC heap stays private. Host strings and bytes are copied
through linear memory; typed WIT values cross the canonical ABI. wasm-tools assembles the component;
Snek encodes the enclosed core program first, optionally optimizing it with Binaryen.
"""
from pathlib import Path

from . import wasi_bindings as library
from .runtime_abi import abi_component_base_imports, abi_component_input_imports

WIT = Path(__file__).with_name("wit")
RUNTIME = Path(__file__).with_name("component_runtime.wat")
INPUT_RUNTIME = Path(__file__).with_name("component_input.wat")

# Core imports implemented by canonical built-ins or lowered WASI functions.
IMPORTS = abi_component_base_imports()
INPUT_IMPORTS = abi_component_input_imports()


def core_imports(*, stdin=False, features=()):
    imports = IMPORTS | (INPUT_IMPORTS if stdin else {}) | library.imports(features)
    return '\n'.join(f'(import "snek:component" "{n}" (func $io.{n} {sig}))'
                     for n, sig in imports.items()) + '\n(import "snek:component" "memory" (memory 1))'


def command_wit(*, stdin=False, features=()):
    world = (WIT / "command.wit").read_text()
    if stdin:
        world = world.replace("    export", "    import wasi:cli/stdin@0.3.0;\n    export")
    for name in library.world_imports(features):
        world = world.replace("    export", f"    import {name};\n    export")
    deps = (WIT / "deps/cli.wit").read_text().split("package wasi:cli@0.3.0;", 1)[1]
    out = world + "\npackage wasi:cli@0.3.0 {\n" + deps + "\n}\n"
    for package in ("clocks", "random"):
        if any(name.startswith(f"wasi:{package}/") for name in library.world_imports(features)):
            rest = (WIT / f"deps/{package}.wit").read_text().split(f"package wasi:{package}@0.3.0;", 1)[1]
            out += f"\npackage wasi:{package}@0.3.0 {{\n{rest}\n}}\n"
    return out


def wrap(core: str) -> str:
    stdin = '(import "snek:component" "stdin"' in core
    features = library.features(core)
    bindings = ' '.join(f'(export "{n}" (func ${n}))' for n in (IMPORTS | (INPUT_IMPORTS if stdin else {}) | library.imports(features)))
    arena = library.needs_arena(features)
    memory_body = (library.MEMORY.read_text() + '''
      (export "realloc" (func $abi.realloc)) (export "alloc" (func $abi.alloc)) (export "reset" (func $abi.reset))
''') if arena else ""
    realloc_alias = '(alias core export $memory "realloc" (core func $abi-realloc))' if arena else ""
    input_type = '''
      (type $input (instance
        (alias outer 1 $error (type $e))
        (export "error-code" (type $error (eq $e)))
        (type $bytes (stream u8))
        (type $result (result (error $error)))
        (type $future (future $result))
        (type $read (func (result (tuple $bytes $future))))
        (export "read-via-stream" (func (type $read)))))
      (import "wasi:cli/stdin@0.3.0" (instance $stdin (type $input)))
      (alias export $stdin "read-via-stream" (func $stdin))
''' if stdin else ""
    input_functions = '''
      (core func $stdin (canon lower (func $stdin) (memory $memory)))
      (core func $stream-read (canon stream.read $bytes (memory $memory) async))
      (core func $stream-drop-reader (canon stream.drop-readable $bytes))
      (core func $stream-cancel-read (canon stream.cancel-read $bytes))
      (core func $future-cancel-read (canon future.cancel-read $future))
''' if stdin else ""
    core = core.replace("(module", "(core module $program", 1)
    return f'''(component
      (type $types (instance
        (type $e (enum "io" "illegal-byte-sequence" "pipe"))
        (export "error-code" (type (eq $e)))))
      (import "wasi:cli/types@0.3.0" (instance $types (type $types)))
      (alias export $types "error-code" (type $error))
      {input_type}
      (type $output (instance
        (alias outer 1 $error (type $e))
        (export "error-code" (type $error (eq $e)))
        (type $bytes (stream u8))
        (type $result (result (error $error)))
        (type $future (future $result))
        (type $write (func (param "data" $bytes) (result $future)))
        (export "write-via-stream" (func (type $write)))))
      (import "wasi:cli/stdout@0.3.0" (instance $stdout (type $output)))
      ;; Use a fresh instance type for each re-exported error-code identity.
      ;; Reusing $output here triggers a wasm-tools 1.250 subtype-mapping assertion.
      (type $error-output (instance
        (alias outer 1 $error (type $e))
        (export "error-code" (type $error (eq $e)))
        (type $bytes (stream u8))
        (type $result (result (error $error)))
        (type $future (future $result))
        (type $write (func (param "data" $bytes) (result $future)))
        (export "write-via-stream" (func (type $write)))))
      (import "wasi:cli/stderr@0.3.0" (instance $stderr (type $error-output)))
      (import "wasi:cli/exit@0.3.0" (instance $exit (export "exit-with-code" (func (param "status-code" u8)))))
      (alias export $stdout "write-via-stream" (func $stdout))
      (alias export $stderr "write-via-stream" (func $stderr))
      (alias export $exit "exit-with-code" (func $exit))
      {library.interface_imports(features)}
      (type $bytes (stream u8))
      (type $io-result (result (error $error)))
      (type $future (future $io-result))
      (type $result (result))
      (type $run (func async (result $result)))
      (core module $memory (memory (export "memory") 1) {memory_body})
      (core instance $memory (instantiate $memory))
      (alias core export $memory "memory" (core memory $memory))
      {realloc_alias}
      {library.lowered_functions(features)}
      (core func $stdout (canon lower (func $stdout)))
      (core func $stderr (canon lower (func $stderr)))
      (core func $exit (canon lower (func $exit)))
      {input_functions}
      (core func $stream-new (canon stream.new $bytes))
      (core func $stream-write (canon stream.write $bytes (memory $memory) async))
      (core func $stream-drop (canon stream.drop-writable $bytes))
      (core func $future-read (canon future.read $future (memory $memory) async))
      (core func $future-drop (canon future.drop-readable $future))
      (core func $set-new (canon waitable-set.new))
      (core func $set-drop (canon waitable-set.drop))
      (core func $join (canon waitable.join))
      (core func $task-return (canon task.return (result $result) (memory $memory)))
      (core func $task-cancel (canon task.cancel))
      (core instance $host {bindings} (export "memory" (memory $memory)))
      {core}
      (core instance $program (instantiate $program (with "snek:component" (instance $host))))
      (alias core export $program "run" (core func $run))
      (alias core export $program "callback" (core func $callback))
      (func $run (type $run) (canon lift (core func $run) (memory $memory) async (callback $callback)))
      (instance $run (export "run" (func $run)))
      (export "wasi:cli/run@0.3.0" (instance $run)))
'''
