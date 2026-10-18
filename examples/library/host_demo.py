from snek import host, i32


@host("get_name")
def get_name() -> str:
    pass


@host("render")
def render(text: str) -> None:
    pass


def increment(count: i32) -> i32:
    count += 1
    render("Hello, " + get_name() + "! Clicks: " + str(count))
    return count
