# error: gather() requires matching non-None coroutine result types
from snek import checkpoint, gather
gather(checkpoint())
