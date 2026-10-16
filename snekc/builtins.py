"""Built-in classes, written in snek itself.

This source is parsed and declared ahead of every user module, so the
exception hierarchy goes through the same checker and emitter as user classes:
`class MyError(ValueError)` is ordinary single inheritance, `except
LookupError` is a `ref.test`, and the runtime (prelude.wat) throws instances of
these classes for IndexError, ZeroDivisionError and friends.

The message lives in a hidden field (`__message`), so user code reads it the
CPython way, through `str(e)`. It is None when the exception was created
without an argument, which is how `str(KeyError())` ('') differs from
`str(KeyError(""))` ("''"). The constructor parameter is spelled `__msg` so
that `ValueError(message=...)` is rejected, as it is in CPython, and the
checker only lets user code pass a str for it.
"""

BUILTINS_SOURCE = '''
class BaseException:
    __message: Final[str | None]
    __keyrepr: Final[str | None] = None

    def __init__(self, __msg: str | None = None) -> None:
        self.__message = __msg


class Exception(BaseException):
    pass


class GeneratorExit(BaseException):
    pass


class StopIteration(Exception):
    pass


class ArithmeticError(Exception):
    pass


class ZeroDivisionError(ArithmeticError):
    pass


class OverflowError(ArithmeticError):
    pass


class AssertionError(Exception):
    pass


class LookupError(Exception):
    pass


class IndexError(LookupError):
    pass


class KeyError(LookupError):
    pass


class RuntimeError(Exception):
    pass


class OSError(Exception):
    pass


class BrokenPipeError(OSError):
    pass


class NotImplementedError(RuntimeError):
    pass


class ValueError(Exception):
    pass


class TypeError(Exception):
    pass


class NameError(Exception):
    pass


class UnboundLocalError(NameError):
    pass


class MemoryError(Exception):
    pass
'''


# The str methods, also written in snek. `s.find(x)` compiles to a call of
# `__str_find(s, x)`; a function is only checked and emitted when a program
# uses it. These bodies can call the runtime intrinsics listed in check.py
# (`__blen`, `__dec`, `__bfind`, the `__buf` string builder, ...), which user
# code cannot. Strings are UTF-8, so positions in these bodies are byte
# offsets unless a name says otherwise; `__cplen`/`__cpoff`/`__cpidx` convert
# between code points and bytes. `$NAME` is a Unicode flag from unicode.py.
# Parameters before `/` are positional-only, as in CPython.

STR_METHODS_TEMPLATE = """
def __adj_start(i: i32, n: i32) -> i32:
    if i < 0:
        i += n
        if i < 0:
            return 0
    return i


def __adj_end(i: i32, n: i32) -> i32:
    if i > n:
        return n
    if i < 0:
        i += n
        if i < 0:
            return 0
    return i


def __str_find(s: str, sub: str, start: i32 = 0, end: i32 = 2147483647, /) -> i32:
    n = __cplen(s)
    start = __adj_start(start, n)
    end = __adj_end(end, n)
    if end - start < __cplen(sub):
        return -1
    if __blen(sub) == 0:
        return start
    p = __bfind(s, sub, __cpoff(s, start), __cpoff(s, end))
    if p < 0:
        return -1
    return __cpidx(s, p)


def __str_rfind(s: str, sub: str, start: i32 = 0, end: i32 = 2147483647, /) -> i32:
    n = __cplen(s)
    start = __adj_start(start, n)
    end = __adj_end(end, n)
    if end - start < __cplen(sub):
        return -1
    if __blen(sub) == 0:
        return end
    p = __brfind(s, sub, __cpoff(s, start), __cpoff(s, end))
    if p < 0:
        return -1
    return __cpidx(s, p)


def __str_index(s: str, sub: str, start: i32 = 0, end: i32 = 2147483647, /) -> i32:
    i = __str_find(s, sub, start, end)
    if i < 0:
        raise ValueError("substring not found")
    return i


def __str_rindex(s: str, sub: str, start: i32 = 0, end: i32 = 2147483647, /) -> i32:
    i = __str_rfind(s, sub, start, end)
    if i < 0:
        raise ValueError("substring not found")
    return i


def __str_count(s: str, sub: str, start: i32 = 0, end: i32 = 2147483647, /) -> i32:
    n = __cplen(s)
    start = __adj_start(start, n)
    end = __adj_end(end, n)
    if end - start < __cplen(sub):
        return 0
    m = __blen(sub)
    if m == 0:
        return end - start + 1
    a = __cpoff(s, start)
    b = __cpoff(s, end)
    c = 0
    while True:
        p = __bfind(s, sub, a, b)
        if p < 0:
            return c
        c += 1
        a = p + m


def __str_startswith(s: str, prefix: str, start: i32 = 0, end: i32 = 2147483647, /) -> bool:
    n = __cplen(s)
    start = __adj_start(start, n)
    end = __adj_end(end, n) - __cplen(prefix)
    if end < start:
        return False
    return __bmatch(s, __cpoff(s, start), prefix)


def __str_endswith(s: str, suffix: str, start: i32 = 0, end: i32 = 2147483647, /) -> bool:
    n = __cplen(s)
    start = __adj_start(start, n)
    end = __adj_end(end, n) - __cplen(suffix)
    if end < start:
        return False
    return __bmatch(s, __cpoff(s, end), suffix)


def __str_removeprefix(s: str, prefix: str, /) -> str:
    if __bmatch(s, 0, prefix):
        return __bsub(s, __blen(prefix), __blen(s))
    return s


def __str_removesuffix(s: str, suffix: str, /) -> str:
    m = __blen(suffix)
    n = __blen(s)
    if m > 0 and m <= n and __bmatch(s, n - m, suffix):
        return __bsub(s, 0, n - m)
    return s


def __space_at(s: str, i: i32) -> bool:
    return __has(__dec(s, i), $SPACE)


def __space_before(s: str, i: i32) -> bool:
    return __has(__dec(s, __back(s, i)), $SPACE)


def __str_split(s: str, sep: str | None = None, maxsplit: i32 = -1) -> list[str]:
    out: list[str] = []
    n = __blen(s)
    if maxsplit < 0:
        maxsplit = 2147483647
    i = 0
    if sep is None:
        while maxsplit > 0:
            while i < n and __space_at(s, i):
                i += __wat(s, i)
            if i == n:
                break
            j = i
            i += __wat(s, i)
            while i < n and not __space_at(s, i):
                i += __wat(s, i)
            out.append(__bsub(s, j, i))
            maxsplit -= 1
        if i < n:
            while i < n and __space_at(s, i):
                i += __wat(s, i)
            if i != n:
                out.append(__bsub(s, i, n))
        return out
    m = __blen(sep)
    if m == 0:
        raise ValueError("empty separator")
    while maxsplit > 0:
        p = __bfind(s, sep, i, n)
        if p < 0:
            break
        out.append(__bsub(s, i, p))
        i = p + m
        maxsplit -= 1
    out.append(__bsub(s, i, n))
    return out


def __str_rsplit(s: str, sep: str | None = None, maxsplit: i32 = -1) -> list[str]:
    out: list[str] = []
    n = __blen(s)
    if maxsplit < 0:
        maxsplit = 2147483647
    if sep is None:
        i = n
        while maxsplit > 0:
            while i > 0 and __space_before(s, i):
                i = __back(s, i)
            if i == 0:
                break
            j = i
            i = __back(s, i)
            while i > 0 and not __space_before(s, i):
                i = __back(s, i)
            out.append(__bsub(s, i, j))
            maxsplit -= 1
        if i > 0:
            while i > 0 and __space_before(s, i):
                i = __back(s, i)
            if i > 0:
                out.append(__bsub(s, 0, i))
        out.reverse()
        return out
    m = __blen(sep)
    if m == 0:
        raise ValueError("empty separator")
    j = n
    while maxsplit > 0:
        p = __brfind(s, sep, 0, j)
        if p < 0:
            break
        out.append(__bsub(s, p + m, j))
        j = p
        maxsplit -= 1
    out.append(__bsub(s, 0, j))
    out.reverse()
    return out


def __linebreak(cp: i32) -> bool:
    if cp < 14:
        return cp >= 10
    if cp < 31:
        return cp >= 28
    return cp == 133 or cp == 8232 or cp == 8233


def __str_splitlines(s: str, keepends: bool = False) -> list[str]:
    out: list[str] = []
    n = __blen(s)
    i = 0
    j = 0
    while i < n:
        while i < n and not __linebreak(__dec(s, i)):
            i += __wat(s, i)
        eol = i
        if i < n:
            if __byte(s, i) == 13 and i + 1 < n and __byte(s, i + 1) == 10:
                i += 2
            else:
                i += __wat(s, i)
            if keepends:
                eol = i
        out.append(__bsub(s, j, eol))
        j = i
    return out


def __str_join(sep: str, parts: list[str], /) -> str:
    n = len(parts)
    if n == 0:
        return ""
    if n == 1:
        return parts[0]
    total = __blen(sep) * (n - 1)
    i = 0
    while i < n:
        total += __blen(parts[i])
        i += 1
    b = __buf(total)
    __puts(b, parts[0])
    i = 1
    while i < n:
        __puts(b, sep)
        __puts(b, parts[i])
        i += 1
    return __done(b)


def __cp_in(chars: str, cp: i32) -> bool:
    n = __blen(chars)
    i = 0
    while i < n:
        c = __dec(chars, i)
        if c == cp:
            return True
        i += __cpw(c)
    return False


def __strip_char(cp: i32, chars: str | None) -> bool:
    if chars is None:
        return __has(cp, $SPACE)
    return __cp_in(chars, cp)


def __strip(s: str, chars: str | None, left: bool, right: bool) -> str:
    i = 0
    j = __blen(s)
    if left:
        while i < j and __strip_char(__dec(s, i), chars):
            i += __wat(s, i)
    if right:
        while j > i and __strip_char(__dec(s, __back(s, j)), chars):
            j = __back(s, j)
    return __bsub(s, i, j)


def __str_strip(s: str, chars: str | None = None, /) -> str:
    return __strip(s, chars, True, True)


def __str_lstrip(s: str, chars: str | None = None, /) -> str:
    return __strip(s, chars, True, False)


def __str_rstrip(s: str, chars: str | None = None, /) -> str:
    return __strip(s, chars, False, True)


def __str_replace(s: str, old: str, new: str, /, count: i32 = -1) -> str:
    n = __blen(s)
    m = __blen(old)
    if count < 0:
        count = 2147483647
    if count == 0 or __cplen(s) < __cplen(old):
        return s
    if m == 0:
        if __blen(new) == 0:
            return s
        k = __cplen(s) + 1
        if count < k:
            k = count
        b = __buf(n + k * __blen(new))
        i = 0
        t = 0
        while t < k:
            __puts(b, new)
            if t < k - 1:
                w = __wat(s, i)
                __putsub(b, s, i, i + w)
                i += w
            t += 1
        __putsub(b, s, i, n)
        return __done(b)
    i = 0
    p = __bfind(s, old, 0, n)
    if p < 0:
        return s
    b = __buf(n)
    while count > 0 and p >= 0:
        __putsub(b, s, i, p)
        __puts(b, new)
        i = p + m
        count -= 1
        p = __bfind(s, old, i, n)
    __putsub(b, s, i, n)
    return __done(b)


def __sigma(s: str, i: i32) -> i32:
    # lower() of the capital sigma at byte i: final (U+03C2) after a cased letter
    # unless another cased letter follows, skipping case-ignorable characters
    j = i
    c = 0
    found = False
    while j > 0:
        j = __back(s, j)
        c = __dec(s, j)
        if not __has(c, $CASE_IGNORABLE):
            found = True
            break
    if not found or not __has(c, $CASED):
        return 963
    n = __blen(s)
    j = i + 2
    while j < n:
        c = __dec(s, j)
        if not __has(c, $CASE_IGNORABLE):
            if __has(c, $CASED):
                return 963
            return 962
        j += __cpw(c)
    return 962


def __lower_at(b: __Buf, s: str, i: i32, cp: i32) -> None:
    if cp == 931:
        __put(b, __sigma(s, i))
    else:
        __putcase(b, cp, 1)


def __str_upper(s: str, /) -> str:
    n = __blen(s)
    b = __buf(n)
    i = 0
    while i < n:
        cp = __dec(s, i)
        __putcase(b, cp, 0)
        i += __cpw(cp)
    return __done(b)


def __str_lower(s: str, /) -> str:
    n = __blen(s)
    b = __buf(n)
    i = 0
    while i < n:
        cp = __dec(s, i)
        __lower_at(b, s, i, cp)
        i += __cpw(cp)
    return __done(b)


def __str_casefold(s: str, /) -> str:
    n = __blen(s)
    b = __buf(n)
    i = 0
    while i < n:
        cp = __dec(s, i)
        __putcase(b, cp, 3)
        i += __cpw(cp)
    return __done(b)


def __str_swapcase(s: str, /) -> str:
    n = __blen(s)
    b = __buf(n)
    i = 0
    while i < n:
        cp = __dec(s, i)
        if __has(cp, $UPPER):
            __lower_at(b, s, i, cp)
        elif __has(cp, $LOWER):
            __putcase(b, cp, 0)
        else:
            __put(b, cp)
        i += __cpw(cp)
    return __done(b)


def __str_capitalize(s: str, /) -> str:
    n = __blen(s)
    if n == 0:
        return s
    b = __buf(n)
    cp = __dec(s, 0)
    __putcase(b, cp, 2)
    i = __cpw(cp)
    while i < n:
        cp = __dec(s, i)
        __lower_at(b, s, i, cp)
        i += __cpw(cp)
    return __done(b)


def __str_title(s: str, /) -> str:
    n = __blen(s)
    b = __buf(n)
    prev = False
    i = 0
    while i < n:
        cp = __dec(s, i)
        if prev:
            __lower_at(b, s, i, cp)
        else:
            __putcase(b, cp, 2)
        prev = __has(cp, $CASED)
        i += __cpw(cp)
    return __done(b)


def __str_isspace(s: str, /) -> bool:
    return __all_flags(s, $SPACE)


def __str_isalpha(s: str, /) -> bool:
    return __all_flags(s, $ALPHA)


def __str_isdecimal(s: str, /) -> bool:
    return __all_flags(s, $DECIMAL)


def __str_isdigit(s: str, /) -> bool:
    return __all_flags(s, $DIGIT)


def __str_isnumeric(s: str, /) -> bool:
    return __all_flags(s, $NUMERIC)


def __str_isalnum(s: str, /) -> bool:
    return __all_flags(s, $ALPHA | $DECIMAL | $DIGIT | $NUMERIC)


def __str_isprintable(s: str, /) -> bool:
    return not __any_flags(s, $NONPRINT)


def __str_isascii(s: str, /) -> bool:
    return __blen(s) == __cplen(s)


def __str_isupper(s: str, /) -> bool:
    n = __blen(s)
    if n == 0:
        return False
    if __cplen(s) == 1:
        return __has(__dec(s, 0), $UPPER)
    cased = False
    i = 0
    while i < n:
        cp = __dec(s, i)
        if __has(cp, $LOWER | $TITLE):
            return False
        if __has(cp, $UPPER):
            cased = True
        i += __cpw(cp)
    return cased


def __str_islower(s: str, /) -> bool:
    n = __blen(s)
    if n == 0:
        return False
    if __cplen(s) == 1:
        return __has(__dec(s, 0), $LOWER)
    cased = False
    i = 0
    while i < n:
        cp = __dec(s, i)
        if __has(cp, $UPPER | $TITLE):
            return False
        if __has(cp, $LOWER):
            cased = True
        i += __cpw(cp)
    return cased


def __str_istitle(s: str, /) -> bool:
    n = __blen(s)
    if n == 0:
        return False
    if __cplen(s) == 1:
        return __has(__dec(s, 0), $TITLE | $UPPER)
    cased = False
    prev = False
    i = 0
    while i < n:
        cp = __dec(s, i)
        if __has(cp, $UPPER | $TITLE):
            if prev:
                return False
            prev = True
            cased = True
        elif __has(cp, $LOWER):
            if not prev:
                return False
            prev = True
            cased = True
        else:
            prev = False
        i += __cpw(cp)
    return cased


def __str_isidentifier(s: str, /) -> bool:
    n = __blen(s)
    if n == 0:
        return False
    cp = __dec(s, 0)
    if not __has(cp, $XID_START) and cp != 95:
        return False
    i = __cpw(cp)
    while i < n:
        cp = __dec(s, i)
        if not __has(cp, $XID_CONTINUE):
            return False
        i += __cpw(cp)
    return True


def __fill(c: str) -> i32:
    if __cplen(c) != 1:
        raise TypeError("The fill character must be exactly one character long")
    return __dec(c, 0)


def __pad(s: str, left: i32, right: i32, f: i32) -> str:
    b = __buf(__blen(s) + (left + right) * __cpw(f))
    k = 0
    while k < left:
        __put(b, f)
        k += 1
    __puts(b, s)
    k = 0
    while k < right:
        __put(b, f)
        k += 1
    return __done(b)


def __str_ljust(s: str, width: i32, fillchar: str = " ", /) -> str:
    f = __fill(fillchar)
    n = __cplen(s)
    if width <= n:
        return s
    return __pad(s, 0, width - n, f)


def __str_rjust(s: str, width: i32, fillchar: str = " ", /) -> str:
    f = __fill(fillchar)
    n = __cplen(s)
    if width <= n:
        return s
    return __pad(s, width - n, 0, f)


def __str_center(s: str, width: i32, fillchar: str = " ", /) -> str:
    f = __fill(fillchar)
    n = __cplen(s)
    if width <= n:
        return s
    marg = width - n
    left = marg // 2 + (marg & width & 1)
    return __pad(s, left, marg - left, f)


def __str_zfill(s: str, width: i32, /) -> str:
    n = __cplen(s)
    if n >= width:
        return s
    fill = width - n
    b = __buf(__blen(s) + fill)
    rest = 0
    if n > 0 and (__byte(s, 0) == 43 or __byte(s, 0) == 45):
        __put(b, __byte(s, 0))
        rest = 1
    k = 0
    while k < fill:
        __put(b, 48)
        k += 1
    __putsub(b, s, rest, __blen(s))
    return __done(b)


def __int_space(cp: i32) -> bool:
    # int() strips Unicode whitespace, except that ASCII characters count only if
    # C's isspace() says so (U+001C..U+001F are whitespace to str.strip, not to int)
    if cp < 128:
        return cp == 32 or (cp >= 9 and cp <= 13)
    return __has(cp, $SPACE)


def __parse_int(s: str, bits: i32) -> i64:
    n = __blen(s)
    i = 0
    while i < n and __int_space(__dec(s, i)):
        i += __wat(s, i)
    neg = False
    if i < n and (__byte(s, i) == 43 or __byte(s, i) == 45):
        neg = __byte(s, i) == 45
        i += 1
    v: i64 = 0
    digits = 0
    over = False
    prev_us = True
    while i < n:
        cp = __dec(s, i)
        if cp == 95:
            if prev_us:
                raise ValueError("invalid literal for int() with base 10: " + __repr(s))
            prev_us = True
        else:
            d = __decimal(cp)
            if d < 0:
                break
            prev_us = False
            digits += 1
            if not over:
                # accumulate negatively so -2**63 fits
                if v < -922337203685477580 or (v == -922337203685477580 and d > 8):
                    over = True
                else:
                    v = v * 10 - i64(d)
        i += __cpw(cp)
    if digits == 0 or prev_us:
        raise ValueError("invalid literal for int() with base 10: " + __repr(s))
    if digits > 4300:
        raise ValueError("Exceeds the limit (4300 digits) for integer string conversion: value has " + str(digits)
                         + " digits; use sys.set_int_max_str_digits() to increase the limit")
    while i < n and __int_space(__dec(s, i)):
        i += __wat(s, i)
    if i != n:
        raise ValueError("invalid literal for int() with base 10: " + __repr(s))
    if not neg:
        if v == -9223372036854775808:
            over = True
        v = -v
    if bits == 32 and (v < -2147483648 or v > 2147483647):
        over = True
    if over:
        if bits == 32:
            raise OverflowError("int too large to convert to i32")
        raise OverflowError("int too large to convert to i64")
    return v
"""
