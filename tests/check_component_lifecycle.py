"""Exercise real canonical-ABI cancellation and repeated command invocations.

The outer component acts as a host: it invokes the generated component twice,
either synchronously or with async lowering followed by subtask.cancel. This
checks actual runtime events, not a simulated call to the guest callback.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from snekc import component, driver, runner

NORMAL = '''from snek import run, checkpoint
async def main() -> None:
    print("first")
    await checkpoint()
    print("second")
run(main())
'''
CANCEL = '''from snek import run, checkpoint
async def child() -> None:
    try:
        print("started")
        while True:
            await checkpoint()
    finally:
        await checkpoint()
        print("child cleanup")
async def main() -> None:
    try:
        await child()
    finally:
        await checkpoint()
        print("parent cleanup")
run(main())
'''


def harness(guest, cancel):
    stdin = '(import "wasi:cli/stdin@0.3.0"' in guest
    features = component.library.features(guest)
    markers = ''.join(f'(import "snek:component" "{next(iter(component.library.IMPORTS[f]))}"' for f in sorted(features))
    if stdin:
        markers += '(import "snek:component" "stdin"'
    header = component.wrap(markers).split('(core module $memory', 1)[0]
    nested = guest.replace('(component', '(component $guest', 1)
    bindings = ' '.join(f'(with "wasi:cli/{n}@0.3.0" (instance ${n}))'
                        for n in (("types", "stdout", "stderr", "exit", "stdin") if stdin else
                                  ("types", "stdout", "stderr", "exit")))
    bindings += ' ' + ' '.join(f'(with "{name}" (instance ${name.split("/")[1].split("@")[0]}))'
                              for name in component.library.world_imports(features))
    if cancel:
        lowered = '(core func $call (canon lower (func $guest-run) (memory $memory) async))'
        call_type = '(param i32) (result i32)'
        action = '''(local.set $handle (i32.shr_u (call $call (i32.const 0)) (i32.const 4)))
          (if (i32.eqz (local.get $handle)) (then (unreachable)))
          (if (i32.ne (call $cancel (local.get $handle)) (i32.const 4)) (then (unreachable)))
          (call $drop (local.get $handle))'''
    else:
        lowered = '(core func $call (canon lower (func $guest-run)))'
        call_type = '(result i32)'
        action = '(if (call $call) (then (unreachable)))'
    return header + f'''
      {nested}
      (instance $guest (instantiate $guest {bindings}))
      (alias export $guest "wasi:cli/run@0.3.0" (instance $guest-run))
      (alias export $guest-run "run" (func $guest-run))
      (core module $mem (memory (export "memory") 1))
      (core instance $mem (instantiate $mem))
      (alias core export $mem "memory" (core memory $memory))
      {lowered}
      (core func $cancel (canon subtask.cancel))
      (core func $drop (canon subtask.drop))
      (core func $return (canon task.return (result $result) (memory $memory)))
      (core instance $host (export "call" (func $call)) (export "cancel" (func $cancel))
        (export "drop" (func $drop)) (export "return" (func $return)))
      (core module $test
        (import "host" "call" (func $call {call_type}))
        (import "host" "cancel" (func $cancel (param i32) (result i32)))
        (import "host" "drop" (func $drop (param i32)))
        (import "host" "return" (func $return (param i32)))
        (func (export "run") (result i32) (local $handle i32)
          {action} {action} (call $return (i32.const 0)) (i32.const 0))
        (func (export "callback") (param i32 i32 i32) (result i32) (unreachable)))
      (core instance $test (instantiate $test (with "host" (instance $host))))
      (alias core export $test "run" (core func $run))
      (alias core export $test "callback" (core func $callback))
      (func $run (type $run) (canon lift (core func $run) (memory $memory) async (callback $callback)))
      (instance $run (export "run" (func $run)))
      (export "wasi:cli/run@0.3.0" (instance $run)))'''


def main():
    os.environ["SNEK_RUNTIME"] = "wasmtime"
    with tempfile.TemporaryDirectory(prefix="snek-lifecycle-") as d:
        src, wasm = Path(d, "guest.py"), Path(d, "guest.wasm")
        wat, test = Path(d, "test.wat"), Path(d, "test.wasm")
        for cancel in (False, True):
            for optimize in (False, True):
                src.write_text(CANCEL if cancel else NORMAL)
                res = driver.build(src, wasm, optimize=optimize, component=True)
                wat.write_text(harness(res.wat, cancel))
                driver.wasm_tools("parse", str(wat), "-o", str(test))
                driver.wasm_tools("validate", "--features", "cm-async", str(test))
                got = runner.run(test)
                expected = ("started\nchild cleanup\nparent cleanup\n" if cancel else "first\nsecond\n") * 2
                assert got.code == 0 and got.stdout == expected, (got.code, got.stdout, got.stderr)
                print("ok", "cancel" if cancel else "repeat", "-O3" if optimize else "raw")
    return 0


if __name__ == "__main__":
    sys.exit(main())
