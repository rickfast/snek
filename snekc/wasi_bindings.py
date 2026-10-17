"""Demand-driven bindings for the small WASI packages used by the library."""
from pathlib import Path
from .runtime_abi import (abi_library_operations, abi_component_library_imports,
                          abi_component_arena_imports, abi_needs_arena)


OPERATIONS = abi_library_operations()
IMPORTS = abi_component_library_imports()
ARENA_IMPORTS = abi_component_arena_imports()
MEMORY = Path(__file__).with_name("abi_memory.wat")


def needs_arena(features):
    return abi_needs_arena(list(features))


def imports(features):
    out = {}
    for feature in sorted(features):
        out.update(IMPORTS[feature])
    if needs_arena(features):
        out.update(ARENA_IMPORTS)
    return out


def features(core):
    return {feature for feature, bindings in IMPORTS.items()
            if any(f'(import "snek:component" "{name}"' in core for name in bindings)}


def interface_imports(selected):
    out = []
    if set(selected) & {"arguments", "environment"}:
        out.append('''
      (type $environment-type (instance
        (type $args (list string))
        (type $env (list (tuple string string)))
        (export "get-arguments" (func (result $args)))
        (export "get-environment" (func (result $env)))))
      (import "wasi:cli/environment@0.3.0" (instance $environment (type $environment-type)))
''')
    if "system-clock" in selected:
        out.append('''
      (type $system-clock-type (instance
        (type $record (record (field "seconds" s64) (field "nanoseconds" u32)))
        (export "instant" (type $instant (eq $record)))
        (export "now" (func (result $instant)))))
      (import "wasi:clocks/system-clock@0.3.0" (instance $system-clock (type $system-clock-type)))
''')
    if set(selected) & {"monotonic-clock", "sleep"}:
        out.append('''
      (type $monotonic-clock-type (instance
        (export "now" (func (result u64)))
        (export "wait-for" (func async (param "how-long" u64)))))
      (import "wasi:clocks/monotonic-clock@0.3.0" (instance $monotonic-clock (type $monotonic-clock-type)))
''')
    if "random" in selected:
        out.append('''
      (type $random-type (instance
        (type $bytes (list u8))
        (export "get-random-bytes" (func (param "max-len" u64) (result $bytes)))))
      (import "wasi:random/random@0.3.0" (instance $random (type $random-type)))
''')
    return ''.join(out)


def lowered_functions(selected):
    out = []
    allocator = ' (memory $memory) (realloc $abi-realloc)'
    for feature, instance, name in (("arguments", "environment", "get-arguments"),
                                    ("environment", "environment", "get-environment"),
                                    ("system-clock", "system-clock", "now"),
                                    ("monotonic-clock", "monotonic-clock", "now"),
                                    ("sleep", "monotonic-clock", "wait-for"),
                                    ("random", "random", "get-random-bytes")):
        if feature not in selected:
            continue
        core = next(iter(IMPORTS[feature]))
        options = allocator if feature in ("arguments", "environment", "random") else (
            ' (memory $memory) async' if feature == "sleep" else ' (memory $memory)' if feature == "system-clock" else '')
        out.append(f'(alias export ${instance} "{name}" (func ${core}))\n'
                   f'(core func ${core} (canon lower (func ${core}){options}))')
    if "sleep" in selected:
        out += ['(core func $lib-subtask-cancel (canon subtask.cancel))',
                '(core func $lib-subtask-drop (canon subtask.drop))']
    if needs_arena(selected):
        out += ['(alias core export $memory "reset" (core func $lib-reset-arena))',
                '(alias core export $memory "alloc" (core func $lib-alloc))']
    return '\n'.join(out)


def world_imports(selected):
    names = []
    if set(selected) & {"arguments", "environment"}:
        names.append("wasi:cli/environment@0.3.0")
    if "system-clock" in selected:
        names.append("wasi:clocks/system-clock@0.3.0")
    if set(selected) & {"monotonic-clock", "sleep"}:
        names.append("wasi:clocks/monotonic-clock@0.3.0")
    if "random" in selected:
        names.append("wasi:random/random@0.3.0")
    return names
