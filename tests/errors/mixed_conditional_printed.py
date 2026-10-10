# error: this can be an int at run time (one branch of a conditional expression is an int)
def main(c: bool) -> None:
    print(1 if c else 2.5)
