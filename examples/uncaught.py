"""An exception that escapes main(): stdout is flushed, `Name: message` goes to stderr, exit 1."""
from snek import i32


class ConfigError(ValueError):
    key: str

    def __init__(self, key: str) -> None:
        self.key = key
        super().__init__(f"missing required key '{key}'")


def load(keys: list[str]) -> i32:
    for k in keys:
        print("loading", k)
        if k == "port":
            raise ConfigError(k)
    return len(keys)


def main() -> None:
    try:
        load(["host"])
    finally:
        print("first load done")
    load(["host", "port", "user"])
    print("never printed")


main()
