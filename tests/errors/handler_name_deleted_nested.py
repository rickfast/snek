# error: local variable 'e' may be used before it is assigned
def main() -> None:
    e = ValueError("a")
    try:
        try:
            raise ValueError("b")
        except ValueError as e:
            pass
        raise KeyError("k")
    except KeyError:
        print(e)
