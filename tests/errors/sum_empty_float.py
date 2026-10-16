# error: sum() of a possibly empty float list needs a float start
from snek import f64

def main() -> None:
    values: list[f64] = []
    print(sum(values))
