# error: ValueError has no field 'message'; read the message with str(e)
def f() -> None:
    try:
        pass
    except ValueError as e:
        print(e.message)
