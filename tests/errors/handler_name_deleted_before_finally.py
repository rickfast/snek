# error: local variable 'e' may be used before it is assigned
def main() -> None:
    e = ValueError("orig")
    try:
        raise ValueError("caught")
    except ValueError as e:
        pass
    finally:
        pass
    print(e)  # CPython: UnboundLocalError, the handler deleted e
