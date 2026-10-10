# error: paint() got an unexpected keyword argument 'colour'
def paint(color: str) -> None:
    print(color)


def main() -> None:
    paint(colour="red")
