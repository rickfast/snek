# error: run() is only supported at module scope
from snek import run, checkpoint


def wrong() -> None:
    run(checkpoint())
