# error: a bare `except:` must be the last handler
def f() -> None:
    try:
        pass
    except:
        pass
    except ValueError:
        pass
