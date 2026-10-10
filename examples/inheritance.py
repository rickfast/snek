"""super(), default arguments and keyword arguments."""
from snek import i32, f64


class Vehicle:
    name: str
    wheels: i32
    log: list[str]

    def __init__(self, name: str, wheels: i32 = 4) -> None:
        print(f"  Vehicle.__init__({name}, {wheels})")
        self.name = name
        self.wheels = wheels
        self.log = []
        self.note("created")  # uses self: the object exists from here on

    def note(self, msg: str) -> None:
        self.log.append(f"{self.name}: {msg}")

    def describe(self) -> str:
        return f"{self.name} on {self.wheels} wheels"


class Truck(Vehicle):
    payload: f64

    def __init__(self, name: str, payload: f64 = 1.5, wheels: i32 = 6) -> None:
        print(f"  Truck.__init__({name})")
        self.payload = payload
        super().__init__(name, wheels)  # base fields are assigned in the same phase
        self.note(f"payload {self.payload}")

    def describe(self) -> str:
        return super().describe() + f", carrying {self.payload}t"


class FireTruck(Truck):
    ladder: i32

    def __init__(self, ladder: i32) -> None:
        self.ladder = ladder
        super().__init__("fire truck", wheels=10)

    def describe(self) -> str:
        return "red " + super().describe() + f" and a {self.ladder}m ladder"


class Plain:
    x: i32

    def __init__(self) -> None:
        super().__init__()  # object.__init__(): allowed, does nothing
        self.x = 1


def area(width: f64, height: f64 = 1.0, scale: f64 = 1.0) -> f64:
    return width * height * scale


def tick(log: list[str], label: str) -> i32:
    log.append(label)
    return len(log)


def pair(a: i32, b: i32) -> str:
    return f"a={a} b={b}"


def main() -> None:
    vs: list[Vehicle] = [Vehicle("car"), Truck("lorry"), FireTruck(30), Vehicle(wheels=2, name="bike")]
    for v in vs:
        print(v.describe())
        for line in v.log:
            print("   ", line)
    print(Plain().x)

    print(area(2.0), area(2.0, 3.0), area(2.0, scale=10.0), area(height=4.0, width=0.5))

    # Keyword arguments evaluate in the order written, like CPython
    log: list[str] = []
    print(pair(b=tick(log, "first"), a=tick(log, "second")), "order:", log[0], log[1])


main()
