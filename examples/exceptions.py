"""Exceptions: user hierarchies, handlers, re-raise, and runtime errors as real exceptions."""
from snek import i32, f64


class BankError(Exception):
    pass


class InsufficientFunds(BankError):
    needed: f64

    def __init__(self, needed: f64) -> None:
        self.needed = needed
        super().__init__(f"need {needed} more")


class AccountLocked(BankError):
    pass


class Account:
    owner: str
    balance: f64
    locked: bool = False

    def __init__(self, owner: str, balance: f64) -> None:
        self.owner = owner
        self.balance = balance

    def withdraw(self, amount: f64) -> f64:
        if self.locked:
            raise AccountLocked(f"{self.owner}'s account is locked")
        if amount > self.balance:
            raise InsufficientFunds(amount - self.balance)
        self.balance -= amount
        return self.balance


def try_withdraw(acct: Account, amount: f64) -> str:
    try:
        left = acct.withdraw(amount)
    except InsufficientFunds as e:
        return f"declined: {e} (short by {e.needed})"
    except BankError as e:
        return f"bank error {type(e).__name__}: {e}"
    else:
        return f"ok, {left} left"


def parse_digit(c: i32) -> i32:
    if c < 0 or c > 9:
        raise ValueError(f"not a digit: {c}")
    return c


def checked_sum(xs: list[i32]) -> i32:
    total = 0
    for x in xs:
        try:
            total += parse_digit(x)
        except ValueError as e:
            print("  skipping:", e)
            continue
    return total


def reraise_after_logging(n: i32) -> i32:
    try:
        return 100 // n
    except ZeroDivisionError:
        print("  logging the error, then re-raising")
        raise


def classify(i: i32) -> str:
    xs = [10, 20, 30]
    try:
        if i == 0:
            raise KeyError("missing")
        if i == 1:
            assert i > 5, "i is too small"
        if i == 2:
            return str(xs[i * 10])
        if i == 3:
            return str(xs[0] % (i - 3))
        if i == 4:
            empty: list[i32] = []
            return str(empty.pop())
        if i == 5:
            raise NotImplementedError
        return f"fine: {xs[-1] // i}"
    except (IndexError, KeyError) as e:
        return f"lookup failed ({type(e).__name__}): {e}"
    except ArithmeticError as e:
        return f"math failed: {e}"
    except RuntimeError as e:
        return f"runtime: {type(e).__name__} '{e}'"
    except Exception as e:
        return f"other {type(e).__name__}: {e}"


def main() -> None:
    a = Account("ada", 50.0)
    print(try_withdraw(a, 20.0))
    print(try_withdraw(a, 100.0))
    a.locked = True
    print(try_withdraw(a, 1.0))

    print("sum:", checked_sum([1, 2, 42, 3, -1, 4]))

    try:
        reraise_after_logging(0)
    except ArithmeticError as e:
        print("caught again:", type(e).__name__, "-", e)
    print("ok path:", reraise_after_logging(7))

    for i in range(7):
        print(i, classify(i))

    # Runtime errors from the runtime itself are catchable
    try:
        print(1.5 / 0.0)
    except ZeroDivisionError as e:
        print("float:", e)
    try:
        print(7 / 0)
    except ZeroDivisionError as e:
        print("int true division:", e)
    try:
        print(-7.0 // 0.0)
    except ZeroDivisionError as e:
        print("floor:", e)

    # isinstance on exception objects, and exceptions as ordinary values
    errs: list[Exception] = [ValueError("v"), InsufficientFunds(2.5), KeyError("k"), Exception()]
    for err in errs:
        kind = "bank" if isinstance(err, BankError) else "lookup" if isinstance(err, LookupError) else "plain"
        print(type(err).__name__, kind, f"[{err}]")

    # Nested handlers: the inner one doesn't match, the outer one does
    try:
        try:
            raise AccountLocked("frozen")
        except ValueError:
            print("not reached")
    except BankError as e:
        print("outer caught:", e)

    # Handler that raises a different exception
    try:
        try:
            raise ValueError("first")
        except ValueError as e:
            raise RuntimeError(f"wrapped: {e}")
    except RuntimeError as e:
        print(e)

    # Bare except catches everything, including BaseException subclasses
    try:
        raise BaseException("base")
    except:  # noqa: E722
        print("bare except caught it")


main()
