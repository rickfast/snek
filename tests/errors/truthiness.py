# error: condition must be bool, got list[i32]
def f() -> None:
    xs = [1, 2]
    if xs:
        print("nonempty")
