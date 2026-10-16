from __future__ import annotations
from snek import i32, i64


class Value:
    n: i32
    log: list[str]

    def __init__(self, n: i32, log: list[str]) -> None:
        self.n = n
        self.log = log

    def __eq__(self, other: Value | None) -> bool:
        self.log.append(f"base {self.n}")
        return other is not None and self.n == other.n

    def __str__(self) -> str:
        self.log.append(f"str {self.n}")
        return f"Value({self.n})"

    def __len__(self) -> i32:
        self.log.append(f"len {self.n}")
        return self.n


class Child(Value):
    def __eq__(self, other: Value | None) -> bool:
        self.log.append(f"child {self.n}")
        return super().__eq__(other)

    def __str__(self) -> str:
        return "child " + super().__str__()

    def __len__(self) -> i32:
        return super().__len__() + 1


class Inherited(Value):
    pass


class Never:
    def __eq__(self, other: Never) -> bool:
        print("never eq")
        return False


class Plain:
    pass


class Huge:
    def __len__(self) -> i64:
        return 3000000000


class CustomError(ValueError):
    def __str__(self) -> str:
        return "custom " + super().__str__()


class Talk:
    def __str__(self) -> str:
        print("render")
        return "talk"


class Raising:
    def __eq__(self, other: Raising) -> bool:
        raise ValueError("equality failed")


def mutate(value: Value) -> i32:
    value.n += 1
    return value.n


def make(n: i32, log: list[str]) -> Value:
    log.append(f"make {n}")
    return Value(n, log)


def main() -> None:
    log: list[str] = []
    values: list[Value] = [Value(1, log), Child(1, log), Inherited(1, log)]
    for a in values:
        for b in values:
            print(a == b, a != b, a is b)
    print(log)
    log.clear()
    a = values[0]
    print(a == None, None == a, a != None)
    optional: Value | None = None
    print(optional == a, optional == None, a == optional)
    print(make(2, log) == make(2, log))
    print(log)
    log.clear()
    print(a, mutate(a), a)
    print(str(a), mutate(a), f"{a}", f"{a!s:>14}")
    print(log)
    for v in values:
        print(str(v), len(v), bool(v), v.__len__())
    print(len(Huge()), bool(Huge()))
    negative = Value(-1, log)
    for flag in [False, True]:
        try:
            if flag:
                print(len(negative))
            else:
                print(bool(negative))
        except ValueError as error:
            print(error)
    print(negative.__len__())
    print(Value(1, log) in values, values.count(Value(1, log)), values == values.copy())
    values.remove(Value(1, log))
    print(len(values))
    print((a,) == (Value(a.n, log),), {1: a} == {1: Value(a.n, log)})
    never = Never()
    print(never == never, [never] == [never], never in [never], [never].count(never))
    plain = Plain()
    print(plain == plain, plain == Plain(), bool(plain))
    error: BaseException = CustomError("argument")
    print(error, str(error), repr(error), type(error).__name__, type(error) is CustomError)
    print(CustomError.__name__)
    printable: Value | None = None
    print(printable)
    printable = a
    print(printable)
    print(Talk(), Talk())
    try:
        print(Raising() in [Raising()])
    except ValueError as failure:
        print(failure)


main()
