"""Bundle a prebuilt native executable into npm tarballs and a platform wheel.

No install hooks, runtime downloads, registry credentials, or publishing.
The Rust target must match the supplied executable; release CI builds natively.
"""
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parents[1]
TARGETS = {
    "aarch64-apple-darwin": ("darwin", "arm64", "macosx_11_0_arm64"),
    "x86_64-apple-darwin": ("darwin", "x64", "macosx_11_0_x86_64"),
    "x86_64-unknown-linux-gnu": ("linux", "x64", "manylinux_2_35_x86_64"),
    "aarch64-unknown-linux-gnu": ("linux", "arm64", "manylinux_2_39_aarch64"),
    "x86_64-pc-windows-msvc": ("win32", "x64", "win_amd64"),
}


def npm_tarball(path, files):
    with tarfile.open(path, "w:gz") as archive:
        for name, (data, executable) in files.items():
            info = tarfile.TarInfo("package/" + name)
            info.size, info.mode = len(data), 0o755 if executable else 0o644
            archive.addfile(info, io.BytesIO(data))


def binary_platform(data):
    if data[:4] == b"\xcf\xfa\xed\xfe":
        cpu = int.from_bytes(data[4:8], "little")
        return "darwin", {0x1000007: "x64", 0x100000C: "arm64"}.get(cpu)
    if data[:6] == b"\x7fELF\x02\x01":
        return "linux", {62: "x64", 183: "arm64"}.get(int.from_bytes(data[18:20], "little"))
    if data[:2] == b"MZ":
        offset = int.from_bytes(data[60:64], "little")
        if data[offset:offset + 4] == b"PE\0\0":
            return "win32", {0x8664: "x64"}.get(int.from_bytes(data[offset + 4:offset + 6], "little"))
    raise ValueError("not a supported native executable")


def build(binary: Path, target: str, out: Path):
    version = tomllib.loads((ROOT / "native/Cargo.toml").read_text())["package"]["version"]
    platform, arch, tag = TARGETS[target]
    data = binary.read_bytes()
    if binary_platform(data) != (platform, arch):
        raise ValueError(f"executable architecture does not match {target}")
    # Run the binary to reject accidental stale/wrong-version release inputs.
    reported = subprocess.check_output([str(binary.resolve()), "--version"], text=True).strip()
    if reported != f"snek {version}":
        raise ValueError(f"binary version {reported!r} does not match {version}")
    executable = "snek.exe" if platform == "win32" else "snek"
    out.mkdir(parents=True, exist_ok=True)
    repository = {"type": "git", "url": "git+https://github.com/rickfast/snek.git"}
    common = {"version": version, "repository": repository,
              "description": "The self-hosted Snek compiler and embedded WebAssembly runtime"}
    name = f"snek-lang-{platform}-{arch}"
    native_meta = dict(common, name=name, os=[platform], cpu=[arch], files=["bin/"])
    if platform == "linux":
        native_meta["libc"] = ["glibc"]
    npm_tarball(out / f"{name}-{version}.tgz", {
        "package.json": (json.dumps(native_meta, indent=2).encode(), False),
        f"bin/{executable}": (data, True),
    })
    meta = dict(common, name="snek-lang", engines={"node": ">=18"},
                bin={"snek": "bin/snek.cjs", "snek-lang": "bin/snek.cjs"},
                files=["bin/"], optionalDependencies={
                    f"snek-lang-{os_name}-{cpu}": version for os_name, cpu, _ in TARGETS.values()})
    npm_tarball(out / f"snek-lang-{version}.tgz", {
        "package.json": (json.dumps(meta, indent=2).encode(), False),
        "bin/snek.cjs": ((ROOT / "packaging/npm/snek.cjs").read_bytes(), True),
    })

    dist_info = f"snek_lang-{version}.dist-info"
    files = {
        "snek_cli/__init__.py": (ROOT / "packaging/python/__init__.py").read_bytes(),
        f"snek_cli/bin/{executable}": data,
        f"{dist_info}/METADATA": (
            f"Metadata-Version: 2.1\nName: snek-lang\nVersion: {version}\n"
            "Summary: Self-hosted Snek compiler with an embedded WebAssembly runtime\n"
            "Requires-Python: >=3.10\n"
            "Project-URL: Source, https://github.com/rickfast/snek\n\n"
        ).encode(),
        f"{dist_info}/WHEEL": (
            f"Wheel-Version: 1.0\nGenerator: snek-build-packages\nRoot-Is-Purelib: false\nTag: py3-none-{tag}\n"
        ).encode(),
        f"{dist_info}/entry_points.txt": b"[console_scripts]\nsnek = snek_cli:main\nsnek-lang = snek_cli:main\n",
    }
    record = io.StringIO(newline="")
    writer = csv.writer(record)
    for path, content in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b"=").decode()
        writer.writerow([path, "sha256=" + digest, len(content)])
    writer.writerow([f"{dist_info}/RECORD", "", ""])
    files[f"{dist_info}/RECORD"] = record.getvalue().encode()
    wheel = out / f"snek_lang-{version}-py3-none-{tag}.whl"
    with zipfile.ZipFile(wheel, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            info = zipfile.ZipInfo(name)
            info.create_system = 3
            info.external_attr = (0o100755 if name.startswith("snek_cli/bin/") else 0o100644) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, content)
    print(wheel)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--target", choices=TARGETS, required=True)
    parser.add_argument("--out", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    build(args.binary, args.target, args.out)
