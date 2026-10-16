"""Clock queries and cooperative async sleep, measured in seconds."""
import asyncio
import math
from time import monotonic, time


async def sleep(seconds: float, /) -> None:
    if math.isnan(seconds) or seconds < 0.0:
        raise ValueError("sleep length must be non-negative and not NaN")
    if not math.isfinite(seconds) or seconds * 1_000_000_000.0 >= 18446744073709551616.0:
        raise OverflowError("sleep length is too large")
    await asyncio.sleep(seconds)


__all__ = ["time", "monotonic", "sleep"]
