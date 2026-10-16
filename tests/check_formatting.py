"""Seeded format-spec differential stress test (raw and -O3 Wasm).

Run with the same SNEK_RUNTIME / SNEK_WASM_OPT settings as run_tests.py.
"""

import math
import random
import struct
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from snekc import driver, runner  # noqa: E402


def main() -> None:
    rng = random.Random(1729)
    floats = [0.0, -0.0, 0.5, -0.5, 1.25, 1.75, 2.675, 9.999, 99.999, 999.999, 5e-324]
    while len(floats) < 80:
        value = struct.unpack('>d', rng.getrandbits(64).to_bytes(8, 'big'))[0]
        if math.isfinite(value):
            floats.append(value)
    integers = [0, -1, 1, 15, 16, 255, 65535, -(2**63), 2**63 - 1]
    integers += [rng.randrange(-(2**63), 2**63) for _ in range(31)]
    strings = ['', 'a', 'abcdefg', '雪😀ab', 'e\u0301', 'line\nbreak']

    def specs(sample, kinds, count):
        result = set()
        while len(result) < count:
            kind = rng.choice(kinds)
            precision = rng.choice(['', '.0', '.1', '.2', '.3', '.6', '.17', '.25'])
            spec = (rng.choice(['', '<', '>', '^', '=', '*<', '🦊^', '0=']) +
                    rng.choice(['', '+', ' ', '-']) + rng.choice(['', '#']) +
                    rng.choice(['', '0']) + rng.choice(['', '1', '8', '16', '30']) +
                    rng.choice(['', ',', '_']) + precision + kind)
            try:
                format(sample, spec)
            except ValueError:
                continue
            result.add(spec)
        return sorted(result)

    groups = [(floats, 'f64', specs(0.0, ['', 'f', 'F', 'e', 'E', 'g', 'G', '%'], 70)),
              (integers, 'i64', specs(0, ['', 'b', 'd', 'o', 'x', 'X', 'f', 'e', 'g', '%'], 50)),
              (strings, 'str', ['>10', '🦊^8.3s', '08s', '.0', '^9.2', '<6s'])]
    source = ['from snek import i64, f64', 'def main() -> None:']
    expected, cases = [], []
    for group, (values, ty, formats) in enumerate(groups):
        # Format results can contain newlines; repr makes each case one line.
        # Avoid no-precision default float repr, whose approximation is a known
        # independent limitation. Explicit precision exercises exact rounding.
        formats = [s for s in formats if ty != 'f64' or '.' in s or s[-1:] in 'fFeEgG%']
        source.append(f'    values{group}: list[{ty}] = {values!r}')
        source.append(f'    for v{group} in values{group}:')
        for spec in formats:
            source.append(f'        print(repr(f"{{v{group}:{spec}}}"))')
        for value in values:
            for spec in formats:
                expected.append(repr(format(value, spec)))
                cases.append((value, spec))
    source.append('main()')
    with tempfile.TemporaryDirectory() as directory:
        src = Path(directory) / 'formats.py'
        src.write_text('\n'.join(source))
        for optimize in (False, True):
            wasm = Path(directory) / 'formats.wasm'
            driver.build(src, wasm, optimize=optimize)
            result = runner.run(wasm)
            assert result.code == 0, result.stderr
            actual = result.stdout.splitlines()
            assert len(actual) == len(expected), (len(actual), len(expected))
            failures = [(case, want, got) for case, want, got in zip(cases, expected, actual) if want != got]
            assert not failures, f'{len(failures)} mismatches, first ten: {failures[:10]}'
            print(f'{len(cases)} formatting cases passed ({"-O3" if optimize else "raw"})')


if __name__ == '__main__':
    main()
