"""CLI configuration, clocks, async timers, and secure random helpers."""
from snek import os, run, secrets, sys, time


async def main() -> None:
    print("arguments:", sys.argv[1:])
    print("missing environment:", os.getenv("") is None)
    print("configured default:", os.getenv("", "default"))
    print("system clock:", time.time() > 0.0)
    start = time.monotonic()
    await time.sleep(0.002)
    print("monotonic:", time.monotonic() >= start)
    data = secrets.token_bytes(24)
    print("random bytes:", len(data))
    print("hex characters:", len(secrets.token_hex(8)))
    print("single choice:", secrets.randbelow(1))
    try:
        secrets.token_bytes(-1)
    except ValueError as error:
        print(str(error))
    try:
        await time.sleep(-1.0)
    except ValueError as error:
        print(str(error))


run(main())
