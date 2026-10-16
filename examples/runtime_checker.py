"""Standalone checker coverage for string helpers and exception control flow."""
from __future__ import annotations
from snek import i32


class ParseError(ValueError):
    source: str

    def __init__(self, source: str) -> None:
        self.source = source
        super().__init__("bad number: " + source)


def parse_number(source: str) -> i32:
    try:
        return i32(source.strip())
    except ValueError:
        raise ParseError(source)


def total(lines: list[str], log: list[str]) -> i32:
    result = 0
    for line in lines:
        try:
            if line.startswith("#"):
                continue
            if line == "end":
                break
            result += parse_number(line)
        except ParseError as error:
            log.append(str(error))
            print(error.source)
        else:
            log.append("parsed " + line)
        finally:
            log.append("visited " + line)
    return result


def return_through_finally(log: list[str]) -> str:
    try:
        return "answer".upper()
    finally:
        log.append("returned")


def reraise() -> None:
    try:
        raise KeyError("original")
    except KeyError as error:
        error = KeyError("replacement local")
        raise


def main() -> None:
    log: list[str] = []
    print(total([" ٤٢ ", "bad", "#skip", "8", "end", "ignored"], log))
    print(return_through_finally(log))
    print(" | ".join(log))
    print("Straße ΣΑΣ".casefold(), "ΑΣ.".lower(), " hello ".strip().title())
    print("a,b,c".rsplit(sep=",", maxsplit=1), "banana".replace("a", "o", count=2))
    try:
        reraise()
    except (KeyError, IndexError) as error:
        print("caught original", str(error))
    try:
        raise RuntimeError
    except:
        print("bare handler")
    try:
        print("x".index("z"))
    except ValueError as error:
        print(str(error))


main()
