# error: tuples have fixed length
from snek import i32

def main(t: tuple[i32, ...]) -> None:
    print(t)
