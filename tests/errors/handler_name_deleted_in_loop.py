# error: local variable 'e' may be used before it is assigned
def main() -> None:
    e = ValueError("start")
    for i in range(2):
        print(e)  # the second iteration sees e deleted by the handler
        try:
            raise ValueError("x")
        except ValueError as e:
            pass
