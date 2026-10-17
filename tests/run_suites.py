"""Explicit, isolated test suites. Run --list to inspect coverage and prerequisites."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import importlib.util
import os
from pathlib import Path
import runpy
import shlex
import shutil
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"
sys.path.insert(0, str(ROOT))


@dataclass(frozen=True)
class Check:
    script: str
    requirements: tuple[str, ...] = ()
    compiler: str = "python"
    args: tuple[str, ...] = ()


# Every check_*.py must be assigned explicitly; additions cannot silently vanish
# from CI. Repeated scripts below deliberately exercise different compiler paths.
CHECKS = {
    name: Check("check_" + name + ".py") for name in (
        "closures", "keyed_builtins", "lazy_runtime", "compiler_profile",
        "native_bench", "suites", "tooling", "linker", "emission_pipeline", "cli", "runtime_abi", "body_context",
    )
}
CHECKS.update({
    "benchmarks": Check("check_benchmarks.py", ("node",)),
    "wasm": Check("check_wasm.py", ("wasm-tools",)),
    **{name: Check("check_" + name + ".py", ("runtime", "optimizer")) for name in (
        "parser", "frontend", "declarations", "bootstrap", "emitter_support", "machine_lowering",
        "bodies", "aggregate_bodies", "runtime_bodies", "ordered_bodies", "selfhost_bodies", "language_bodies",
        "byte_io", "formatting", "string_fusion",
    )},
    "byte_io": Check("check_byte_io.py", ("runtime", "wasmtime-py")),
    "reference-corpus": Check("run_tests.py", ("runtime", "optimizer")),
    "selfhost_compiler": Check("check_selfhost_compiler.py", ("runtime", "compiler"), "snek"),
    "selfhost-corpus": Check("run_tests.py", ("runtime", "optimizer", "compiler"), "snek"),
    **{name: Check("check_" + name + ".py", ("component-tools", "optimizer", "wasmtime-py", "node"))
       for name in ("components", "component_lifecycle", "stdlib", "host_library")},
    "selfhost_components": Check("check_selfhost_components.py", ("component-tools", "optimizer", "wasmtime-py", "node", "compiler"), "snek", ("--require-no-skips",)),
    "selfhost-component-corpus": Check("check_components.py", ("component-tools", "optimizer", "compiler"), "snek"),
    "selfhost-component-lifecycle": Check("check_component_lifecycle.py", ("component-tools", "optimizer", "compiler"), "snek"),
    "language_components": Check("check_language_components.py", ("component-tools", "optimizer", "wasmtime-py", "checker")),
    "native": Check("check_native.py", ("native", "compiler"), args=("--corpus",)),
    "packages": Check("check_packages.py", ("packages",)),
})

SUITES = {
    "fast": ("suites", "closures", "keyed_builtins", "lazy_runtime", "wasm", "benchmarks", "native_bench", "compiler_profile", "tooling", "linker", "emission_pipeline", "cli", "runtime_abi", "body_context"),
    "tooling": ("suites", "native_bench", "compiler_profile", "lazy_runtime", "tooling"),
    "reference": ("reference-corpus", "byte_io", "formatting", "string_fusion"),
    "bootstrap": ("parser", "frontend", "declarations", "bootstrap", "emitter_support", "machine_lowering"),
    "checkers": ("bodies", "aggregate_bodies", "runtime_bodies", "ordered_bodies", "selfhost_bodies", "language_bodies"),
    "compiler": ("selfhost_compiler",),
    "selfhost": ("selfhost_compiler", "lazy_runtime", "selfhost-corpus"),
    "components": ("components", "stdlib", "host_library", "component_lifecycle"),
    "selfhost-components": ("selfhost-component-corpus", "selfhost_components", "selfhost-component-lifecycle"),
    "checker-components": ("language_components",),
    "native": ("native",),
    "packaging": ("packages",),
}


def validate_inventory(directory=TESTS, checks=CHECKS, suites=SUITES):
    found = {p.name for p in directory.glob("check_*.py")}
    registered = {check.script for check in checks.values() if check.script.startswith("check_")}
    missing = {check.script for check in checks.values() if not (directory / check.script).is_file()}
    selected = {name for names in suites.values() for name in names}
    errors = []
    for label, names in (("unregistered scripts", found - registered),
                         ("missing scripts", missing),
                         ("checks in no suite", set(checks) - selected),
                         ("unknown checks in suites", selected - set(checks))):
        if names:
            errors.append(label + ": " + ", ".join(sorted(names)))
    if errors:
        raise ValueError("; ".join(errors))


def selection(names):
    if "all" in names:
        names = tuple(SUITES)
    return list(dict.fromkeys(check for suite in names for check in SUITES[suite]))


def prerequisites(requirements):
    """Fail before running any tests, never quietly downgrade optimized coverage."""
    from snekc import driver, runner

    def command(name):
        if not shutil.which(name):
            raise RuntimeError(f"required command not found: {name}")

    for requirement in sorted(set(requirements)):
        if requirement == "runtime":
            runtime = runner.available_runtime()
            prerequisites((runtime,))
        elif requirement == "node":
            node = os.environ.get("SNEK_NODE", "node")
            command(node)
            version = subprocess.check_output([node, "--version"], text=True).strip()
            if int(version.removeprefix("v").split(".")[0]) < runner.MIN_NODE:
                raise RuntimeError(f"Node {runner.MIN_NODE}+ required, found {version}")
        elif requirement == "wasmtime-py":
            if importlib.util.find_spec("wasmtime") is None:
                raise RuntimeError("install Python wasmtime (CI uses 49.0.0)")
        elif requirement == "optimizer":
            try:
                optimizer = driver.find_wasm_opt()
            except driver.ToolError as error:
                raise RuntimeError(str(error)) from error
            subprocess.run([*optimizer, "--version"], check=True)
            # Historical tests use this variable to select optimized cases.
            os.environ["SNEK_WASM_OPT"] = shlex.join(optimizer)
        elif requirement == "component-tools":
            command("wasmtime")
            try:
                tools = driver.find_wasm_tools()
            except driver.ToolError as error:
                raise RuntimeError(str(error)) from error
            subprocess.run([*tools, "--version"], check=True)
        elif requirement in ("wasmtime", "wasm-tools"):
            command(requirement)
        elif requirement == "compiler":
            for name in ("compiler.wasm", "compiler.stage1.wasm", "compiler.stage2.wasm", "compiler.stage3.wasm", "compiler-manifest.json"):
                if not (ROOT / "bootstrap/build" / name).is_file():
                    raise RuntimeError("run python bootstrap/bootstrap_compiler.py first (missing " + name + ")")
        elif requirement == "checker":
            if not (ROOT / "bootstrap/build/bodies.wasm").is_file():
                raise RuntimeError("run python bootstrap/bootstrap_checker.py first")
        elif requirement == "native":
            if not (ROOT / "native/target/release" / ("snek.exe" if os.name == "nt" else "snek")).is_file():
                raise RuntimeError("build the release native executable first")
        elif requirement == "packages":
            for name in ("npm", "bun", "uv"):
                command(name)
            if not list((ROOT / "dist").glob("*.whl")) or not list((ROOT / "dist").glob("*.tgz")):
                raise RuntimeError("run packaging/build_packages.py for this platform first")
        else:
            raise ValueError("unknown prerequisite: " + requirement)


def worker(script, args=(), *, allow_skips=False, allowed_reasons=()):
    """Keep script-specific entry points while enforcing one unittest skip policy."""
    results = []
    original_run = unittest.TextTestRunner.run
    original_argv = sys.argv

    def record_run(runner, suite):
        result = original_run(runner, suite)
        results.append(result)
        return result

    unittest.TextTestRunner.run = record_run
    sys.argv = [str(script), *args]
    code = 0
    try:
        try:
            runpy.run_path(str(script), run_name="__main__")
        except SystemExit as error:
            code = error.code if isinstance(error.code, int) else (0 if error.code is None else 1)
    finally:
        unittest.TextTestRunner.run = original_run
        sys.argv = original_argv
    for result in results:
        if result.testsRun == 0:
            print("FAIL: unittest ran zero tests", file=sys.stderr)
            code = 1
        if not result.wasSuccessful():
            code = 1
        for test, reason in result.skipped:
            permitted = allow_skips or reason in allowed_reasons
            print(f"{'ALLOWED' if permitted else 'FAIL'} skip: {test}: {reason}", file=sys.stderr)
            if not permitted:
                code = 1
    return code


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("suites", nargs="*", metavar="SUITE", help="one or more of: " + ", ".join((*SUITES, "all")))
    parser.add_argument("--list", action="store_true", help="show selected scripts and prerequisites; do not run")
    parser.add_argument("--allow-skips", action="store_true", help="development only: report but permit unittest skips")
    parser.add_argument("--allow-skip-reason", action="append", default=[], help="permit only this exact skip reason")
    parser.add_argument("--worker", choices=CHECKS, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    unknown = set(args.suites) - {*SUITES, "all"}
    if unknown:
        parser.error("unknown suites: " + ", ".join(sorted(unknown)))
    if args.worker:
        check = CHECKS[args.worker]
        return worker(TESTS / check.script, check.args, allow_skips=args.allow_skips, allowed_reasons=args.allow_skip_reason)
    try:
        validate_inventory()
        chosen = selection(args.suites or ["fast"])
        for name in chosen:
            check = CHECKS[name]
            print(f"{name}: {check.script} {' '.join(check.args)} [compiler={check.compiler}; requires={','.join(check.requirements) or 'Python'}]", flush=True)
        if args.list:
            return 0
        prerequisites(requirement for name in chosen for requirement in CHECKS[name].requirements)
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as error:
        print("Suite preflight failed: " + str(error), file=sys.stderr)
        return 2
    failed = []
    for name in chosen:
        check = CHECKS[name]
        env = dict(os.environ, SNEK_COMPILER=check.compiler, PYTHONUNBUFFERED="1")
        # Route identity is explicit. Keep runtime/tool paths supplied by caller.
        for key in ("SNEK_FRONTEND", "SNEK_MACHINE_LOWERER", "SNEK_MACHINE_EMITTER", "SNEK_COMPILER_WASM"):
            env.pop(key, None)
        if "component-tools" in check.requirements:
            env["SNEK_RUNTIME"] = "wasmtime"
        command = [sys.executable, str(Path(__file__).resolve()), "--worker", name]
        if args.allow_skips:
            command.append("--allow-skips")
        for reason in args.allow_skip_reason:
            command.extend(("--allow-skip-reason", reason))
        print(f"\n=== {name} ===", flush=True)
        if subprocess.run(command, cwd=ROOT, env=env).returncode:
            failed.append(name)
    print(f"\n{len(chosen) - len(failed)}/{len(chosen)} checks passed" + ("; failed: " + ", ".join(failed) if failed else ""))
    return bool(failed)


if __name__ == "__main__":
    raise SystemExit(main())
