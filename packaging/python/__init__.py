"""Entry point for the snek-lang native distribution; no compiler logic here."""
import os
from pathlib import Path
import subprocess
import sys


def main():
    binary = Path(__file__).parent / "bin" / ("snek.exe" if os.name == "nt" else "snek")
    if not binary.is_file():
        print("snek: native executable missing; reinstall the snek-lang wheel", file=sys.stderr)
        return 2
    args = [str(binary), *sys.argv[1:]]
    try:
        if os.name != "nt":
            os.execv(binary, args)
        return subprocess.call(args)
    except OSError as error:
        print(f"snek: cannot start native executable: {error}", file=sys.stderr)
        return 2
