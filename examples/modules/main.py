"""Run with Snek or CPython; imports are ordinary source-level Python imports."""
from geometry import Point, distance_squared


def main() -> None:
    origin = Point(0.0, 0.0)
    endpoint = Point(3.0, 4.0)
    print(distance_squared(origin, endpoint))
    print(type(endpoint).__name__)


if __name__ == "__main__":
    main()
