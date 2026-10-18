from snek import i32, i64


def greet(name: str) -> str:
    return "Hello, " + name + "!"


def add(a: i32, b: i32) -> i32:
    return a + b


def twice(value: i64) -> i64:
    return value * 2
