"""Context management and typed __len__/__eq__ dispatch."""
from types import TracebackType
from snek import i32, i64


class Box:
    value: i32
    visits: i32

    def __init__(self, value: i32) -> None:
        self.value = value
        self.visits = 0

    def __enter__(self) -> "Box":
        self.visits += 1
        return self

    def __exit__(self, exc_type: type[BaseException] | None,
                 exc: BaseException | None, tb: TracebackType | None) -> None:
        self.visits += 1

    def __len__(self) -> i32:
        return self.value

    def __eq__(self, other: "Box") -> bool:
        return self.value == other.value


def main() -> None:
    boxes: list[Box] = []
    for i in range(64):
        boxes.append(Box(i))
    checksum: i64 = 0
    for i in range(300000):
        with boxes[i % 64] as box:
            checksum += len(box)
            if box == boxes[(i * 7) % 64]:
                checksum += 1
    for box in boxes:
        checksum += box.visits
    print(checksum)


if __name__ == "__main__":
    main()
