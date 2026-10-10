"""finally: runs on every way out of a try (fallthrough, return, break, continue, exceptions)."""
from snek import i32


class Log:
    entries: list[str]

    def __init__(self) -> None:
        self.entries = []

    def add(self, s: str) -> i32:
        self.entries.append(s)
        return len(self.entries)


def ret_in_try(log: Log) -> i32:
    try:
        log.add("try")
        return log.add("return value computed before finally")
    finally:
        log.add("finally after return")


def exception_through_finally(log: Log) -> None:
    try:
        log.add("about to fail")
        raise ValueError("boom")
    finally:
        log.add("cleanup runs while the exception propagates")


def loop_control(log: Log) -> i32:
    total = 0
    for i in range(10):
        try:
            if i == 2:
                continue
            if i == 5:
                break
            total += i
        finally:
            log.add(f"finally {i}")
    return total


def nested(log: Log) -> str:
    try:
        try:
            try:
                return "inner"
            finally:
                log.add("finally 1")
        finally:
            log.add("finally 2")
    finally:
        log.add("finally 3")


def handled_then_finally(log: Log, fail: bool) -> str:
    try:
        if fail:
            raise KeyError("x")
        result = "no error"
    except KeyError as e:
        result = f"handled {e}"
        return result + " (returned from handler)"
    else:
        log.add("else ran")
    finally:
        log.add(f"finally (fail={fail})")
    return result


def raise_in_handler(log: Log) -> None:
    try:
        raise ValueError("original")
    except ValueError:
        log.add("handler raising a new error")
        raise IndexError("from handler")
    finally:
        log.add("finally still runs")


def finally_raises(log: Log) -> None:
    try:
        log.add("try ok")
    finally:
        log.add("finally raises")
        raise RuntimeError("from finally")


def while_with_finally(log: Log) -> i32:
    n = 0
    while True:
        try:
            n += 1
            if n % 2 == 0:
                continue
            if n > 6:
                return n * 100
        finally:
            log.add(f"w{n}")


def show(title: str, log: Log) -> None:
    print(title)
    for e in log.entries:
        print("  ", e)


def main() -> None:
    log = Log()
    print("ret_in_try ->", ret_in_try(log))
    show("ret_in_try", log)

    log = Log()
    try:
        exception_through_finally(log)
    except ValueError as e:
        log.add(f"caught {e}")
    show("exception_through_finally", log)

    log = Log()
    print("loop_control ->", loop_control(log))
    show("loop_control", log)

    log = Log()
    print("nested ->", nested(log))
    show("nested", log)

    log = Log()
    print(handled_then_finally(log, False))
    print(handled_then_finally(log, True))
    show("handled_then_finally", log)

    log = Log()
    try:
        raise_in_handler(log)
    except IndexError as e:
        log.add(f"caught {type(e).__name__}: {e}")
    show("raise_in_handler", log)

    log = Log()
    try:
        finally_raises(log)
    except RuntimeError as e:
        log.add(f"caught {e}")
    show("finally_raises", log)

    log = Log()
    print("while_with_finally ->", while_with_finally(log))
    show("while_with_finally", log)


main()
