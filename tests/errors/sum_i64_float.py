# error: sum() cannot mix i64 elements and f64 start
from snek import i64

def main() -> None:
    values: list[i64] = [1]
    print(sum(values, 0.0))
