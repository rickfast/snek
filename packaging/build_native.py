"""Build a fresh, verified native compiler from the current checkout.

The bootstrap always regenerates its source bundle, proves the fixed point,
and runs the smoke checks before Cargo consumes its artifacts. This command
does not install toolchains, publish packages, or modify global configuration.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def build(*, offline: bool = False) -> None:
    toolchain = tomllib.loads((ROOT / "native/rust-toolchain.toml").read_text())["toolchain"]["channel"]
    env = dict(os.environ)
    env.setdefault("MACOSX_DEPLOYMENT_TARGET", "11.0")
    cargo = ["cargo", "+" + toolchain]
    options = ["--manifest-path", "native/Cargo.toml", "--release", "--locked"]
    if offline:
        options.append("--offline")
    commands = [
        [sys.executable, "bootstrap/bootstrap_compiler.py"],
        [sys.executable, "tests/check_selfhost_compiler.py"],
        [*cargo, "build", *options],
        [*cargo, "test", *options],
    ]
    for command in commands:
        subprocess.run(command, cwd=ROOT, env=env, check=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true", help="require cached Cargo dependencies")
    args = parser.parse_args(argv)
    try:
        build(offline=args.offline)
    except (OSError, subprocess.CalledProcessError) as error:
        print(f"native build failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
