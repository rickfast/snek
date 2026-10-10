# error: local variable 'e' may be used before it is assigned
def f() -> None:
    try:
        pass
    except ValueError as e:
        print(e)
    print(e)
