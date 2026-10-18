"""Offline installs and real execution through npx, bunx, and uvx.

Requires distributions from packaging/build_packages.py and npm, Bun, uv.
All package-manager caches and installs are confined to a temporary directory.
"""
import argparse
import base64
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def wheel_binary(wheel):
    # auditwheel adds directory entries, which are not executable payloads.
    binaries = [info.filename for info in wheel.infolist()
                if info.filename.startswith("snek_cli/bin/") and not info.is_dir()]
    assert len(binaries) == 1, binaries
    return binaries[0]


def check(dist):
    version = tomllib.loads((ROOT / "native/Cargo.toml").read_text())["package"]["version"]
    wheels = list(dist.glob("*.whl"))
    platforms = list(dist.glob("snek-lang-*-*.tgz"))
    assert len(wheels) == len(platforms) == 1, "test one platform's distribution directory at a time"
    with zipfile.ZipFile(wheels[0]) as wheel:
        records = next(name for name in wheel.namelist() if name.endswith("/RECORD"))
        for name, digest, size in csv.reader(io.StringIO(wheel.read(records).decode())):
            if name == records:
                assert not digest and not size
                continue
            data = wheel.read(name)
            assert len(data) == int(size)
            assert digest == "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
        binary_name = wheel_binary(wheel)
        binary = wheel.read(binary_name)
        assert wheel.getinfo(binary_name).external_attr >> 16 & 0o111
    with tarfile.open(platforms[0]) as package:
        metadata = json.load(package.extractfile("package/package.json"))
        name = "package/bin/" + Path(binary_name).name
        assert package.extractfile(name).read() == binary
        assert package.getmember(name).mode & 0o111
        assert metadata["version"] == version
    packaging = runpy.run_path(str(ROOT / "packaging/build_packages.py"))
    assert packaging["binary_platform"](binary) == (metadata["os"][0], metadata["cpu"][0])
    try:
        packaging["binary_platform"](b"not a native executable")
    except ValueError:
        pass
    else:
        raise AssertionError("invalid executable accepted")
    with tarfile.open(dist / f"snek-lang-{version}.tgz") as package:
        launcher = json.load(package.extractfile("package/package.json"))
        assert launcher["name"] == "snek-lang" and launcher["version"] == version
        assert launcher["bin"] == {"snek": "bin/snek.cjs", "snek-lang": "bin/snek.cjs"}
        assert set(launcher["optionalDependencies"].values()) == {version}
        assert launcher["optionalDependencies"][metadata["name"]] == version
        assert "scripts" not in launcher
    print("Wheel RECORD, executable modes, npm/wheel binary identity: OK", flush=True)

    with tempfile.TemporaryDirectory(prefix="snek-packages-") as temporary:
        work = Path(temporary)
        env = {**os.environ, "npm_config_cache": str(work / "npm-cache"),
               "UV_CACHE_DIR": str(work / "uv-cache"), "UV_TOOL_DIR": str(work / "uv-tools"),
               "UV_TOOL_BIN_DIR": str(work / "uv-bin"), "BUN_INSTALL_CACHE_DIR": str(work / "bun-cache")}

        def call(args, *, code=0, stdout=None, stdin=b""):
            result = subprocess.run(list(map(str, args)), cwd=work, env=env, input=stdin,
                                    capture_output=True, timeout=120)
            assert result.returncode == code, (args, result.returncode, result.stdout, result.stderr)
            if stdout is not None:
                assert result.stdout == stdout, (args, result.stdout, result.stderr)
            return result

        npm, npx, bun, uv = [shutil.which(name) for name in ("npm", "npx", "bun", "uv")]
        assert all((npm, npx, bun, uv)), "npm/npx, Bun and uv are required"
        call([npm, "install", "--offline", "--ignore-scripts", "--omit=optional", "--no-audit", "--no-fund",
              dist / f"snek-lang-{version}.tgz", platforms[0]])
        source = work / "hello world.py"
        source.write_text('from snek import sys\nprint("hello", sys.argv[1:])\n')
        routes = {
            "npx": [npx, "--offline", "snek-lang"],
            "bunx": [bun, "x", "--bun", "--no-install", "snek-lang"],
            "uvx": [uv, "tool", "run", "--offline", "--python", sys.executable, "--from", wheels[0], "snek-lang"],
        }
        for route, command in routes.items():
            call([*command, "--version"], stdout=f"snek {version}\n".encode())
            call([*command, "run", source, "--", "世界", "--flag"], stdout="hello ['世界', '--flag']\n".encode())
            call([*command, "run", ROOT / "examples/stdlib_pipe.py"], stdin=b"\x00\xffhello\n", stdout=b"\x00\xffhello\n")
            output = work / f"{route}.wasm"
            call([*command, "build", source, "-o", output])
            assert output.read_bytes().startswith(b"\0asm\x01\0\0\0")
            bad = work / "bad.py"
            bad.write_text("print(missing)\n")
            original = output.read_bytes()
            call([*command, "build", bad, "-o", output], code=1, stdout=b"")
            assert output.read_bytes() == original
            call([*command, "--not-an-option"], code=2)
            print(f"{route}: version, execution, argv, stdin, build, diagnostics and exit codes OK", flush=True)
        native_package = work / "node_modules" / metadata["name"]
        native_package.rename(work / "missing-platform-package")
        missing = call([*routes["npx"], "--version"], code=2)
        assert b"optional dependencies" in missing.stderr
        print("Missing npm platform package fails closed: OK", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    check(args.dist.resolve())
