"""Monomorphized lists: append/pop growth, negative indices, nesting, packed bools."""
from wpy import i32, i64, f64


def sieve(n: i32) -> list[i32]:
    is_prime: list[bool] = []  # stored in a packed (array (mut i8))
    for _ in range(n + 1):
        is_prime.append(True)
    is_prime[0] = False
    is_prime[1] = False
    i = 2
    while i * i <= n:
        if is_prime[i]:
            j = i * i
            while j <= n:
                is_prime[j] = False
                j += i
        i += 1
    primes: list[i32] = []
    for k in range(n + 1):
        if is_prime[k]:
            primes.append(k)
    return primes


def matrix(n: i32) -> list[list[i64]]:
    rows: list[list[i64]] = []
    for r in range(n):
        row: list[i64] = []
        for c in range(n):
            row.append(i64(r) * 1000000000 + i64(c))
        rows.append(row)
    return rows


def main() -> None:
    p = sieve(50)
    print(len(p), "primes:", p)
    print("last", p[-1], "second last", p[-2])
    p[0] += 100
    print("popped", p.pop(), "now", len(p), "first", p[0])
    m = matrix(3)
    m[1][2] = -1
    print(m)
    print([0.5, 1.25, -3.0], [True, False])
    xs = [1.0, 2.0]
    xs.append(3.0)
    print(xs, len(xs))
    empty: list[f64] = []
    print(empty, len(empty))


main()
