# error: 'break' out of a finally block would swallow
def f() -> None:
    for i in range(3):
        try:
            print(i)
        finally:
            break
