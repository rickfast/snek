"""Shared benchmark statistics and source identity, independent of timing mode.

Warm engines, fresh processes, and compiler phase probes deliberately keep
their own measurement boundaries. These helpers never run a timed workload.
"""
from __future__ import annotations

import hashlib
import math
import os
from pathlib import Path
import statistics
import subprocess


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def stats(values):
    if not values or any(not math.isfinite(v) or v < 0 for v in values):
        raise ValueError("invalid measurement")
    median = statistics.median(values)
    return {"median": median, "mad": statistics.median(abs(v - median) for v in values),
            "min": min(values), "max": max(values), "samples": values}


def source_identity(root: Path) -> str:
    """Hash tracked and untracked source inputs, including uncommitted edits.

    Result snapshots are excluded so recording a trial does not change its
    identity. File names and separators prevent ambiguous concatenations.
    """
    paths = subprocess.check_output(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=root).split(b"\0")
    digest = hashlib.sha256()
    extensions = {".py", ".rs", ".toml", ".lock", ".cjs", ".mjs", ".wat", ".wit", ".json", ".snek", ".txt"}
    for name in sorted(set(paths)):
        if not name or name.startswith(b"bench/results/"):
            continue
        path = root / os.fsdecode(name)
        if path.is_file() and path.suffix in extensions:
            digest.update(name + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()
