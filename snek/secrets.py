"""Secure random helpers; bytes are currently represented by list[i32]."""
import secrets as _secrets


def token_bytes(n: int, /) -> list[int]:
    if n < 0:
        raise ValueError("number of bytes must be non-negative")
    return list(_secrets.token_bytes(n))


def token_hex(n: int, /) -> str:
    if n < 0:
        raise ValueError("number of bytes must be non-negative")
    return _secrets.token_hex(n)


def randbelow(n: int, /) -> int:
    if n <= 0:
        raise ValueError("upper bound must be positive")
    return _secrets.randbelow(n)


__all__ = ["token_bytes", "token_hex", "randbelow"]
