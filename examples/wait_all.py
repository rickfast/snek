# component-error: wait_all() is not yet supported for components
from snek import checkpoint, i32, run, wait_all


async def worker(number: i32, events: list[i32]) -> i32:
    events.append(number)
    await checkpoint()
    events.append(number + 10)
    await checkpoint()
    events.append(number + 20)
    return number


async def nested(events: list[i32]) -> None:
    await wait_all(worker(3, events), worker(4, events))


async def main() -> None:
    events: list[i32] = []
    await wait_all(worker(1, events), worker(2, events))
    print(events)
    await wait_all(nested(events))
    print(events)


run(main())
