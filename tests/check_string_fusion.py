"""Seeded decimal-string fusion differential test, raw and -O3.

Uses SNEK_RUNTIME / SNEK_WASM_OPT like run_tests.py.
"""
from __future__ import annotations

import random
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from snekc import driver, runner


def main() -> None:
    rng = random.Random(1984)
    values = [-(2**63), 2**63 - 1, 0]
    for power in range(19):
        for offset in (-1, 0, 1):
            n = 10**power + offset
            values.extend([n, -n])
    values += [rng.randrange(-(2**63), 2**63) for _ in range(500)]
    source = ("from snek import i64\ndef main() -> None:\n"
              f"    values: list[i64] = {values!r}\n"
              "    for v in values:\n"
              "        text = f'雪{v}:{v!r}😀{v!s}'\n"
              "        print(text, len(text))\n"
              "        print(f'{v}{v}', str(v) + '!' + str(v), f'{v}')\n"
              "main()\n")
    expected = ""
    for v in values:
        text = f"雪{v}:{v!r}😀{v!s}"
        expected += f"{text} {len(text)}\n{v}{v} {v}!{v} {v}\n"
    wat = driver.compile_to_wat(source)
    builders = re.findall(r"  \(func \$str\.build\.[^\n]+\n.*?(?=\n  \(func|\n  \(global|\n\))", wat, re.S)
    assert builders, "no fused builders emitted"
    for builder in builders:
        assert builder.count("array.new_default $u8s") == 1
        assert builder.count("call $rt.mkstr") == 1
        assert "call $rt.i64_write" in builder
        assert "call $rt.i64_to_str" not in builder
        assert "array.new_fixed $strs" not in builder
    with tempfile.TemporaryDirectory(prefix="snek-string-fusion-") as directory:
        src = Path(directory, "fusion.py")
        src.write_text(source)
        for optimize in (False, True):
            wasm = Path(directory, "fusion.wasm")
            driver.build(src, wasm, optimize=optimize)
            result = runner.run(wasm)
            assert result.code == 0, result.stderr
            assert result.stdout == expected, "formatted values or code point lengths differ"
            print(f"{len(values)} integer cases passed ({'O3' if optimize else 'raw'})")


if __name__ == "__main__":
    main()
