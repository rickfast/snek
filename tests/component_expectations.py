"""Compare successful emission and explicitly unsupported component programs."""
from contextlib import contextmanager


@contextmanager
def component_expectation(test, source, component):
    first = source.splitlines()[0] if source else ""
    if component and first.startswith("# component-error: "):
        with test.assertRaises(ValueError) as raised:
            yield
        test.assertIn(first.removeprefix("# component-error: "), str(raised.exception))
    else:
        yield
