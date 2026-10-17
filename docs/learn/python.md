# Coming from Python

Snek uses Python syntax, but it is a statically typed subset. The CPython shim
lets supported programs run as Python for testing; it does not make arbitrary
Python code compilable.

| Python habit | In Snek |
| --- | --- |
| `int` and `float` annotations | Import `i32`, `i64`, `f32`, or `f64` from `snek` |
| Untyped function signatures | Annotate parameters and returns; locals are inferred |
| Reassigning a variable to a different type | A local keeps its inferred or annotated type |
| `if items:` | Write `if len(items) > 0:`; conditions require `bool` |
| `i32`-sized values that later grow arbitrarily | Integers have fixed width and overflow wraps |
| `T | None` for any type | Optional types are limited to reference values |
| Numeric `dict.get(key)` | Supply a numeric default: `values.get(key, 0)` |
| Arbitrary package imports | Only recognized type/library imports; no general module loader |
| `asyncio` task scheduling | Sequential typed coroutines, `run`, `checkpoint`, streams, and timers |
| Relying on generator garbage collection for cleanup | Explicitly close unfinished generators |

## Imports are a language boundary

Supported namespaces include `snek.sys`, `snek.io`, `snek.os`, `snek.time`, and
`snek.secrets`, plus recognized annotation imports such as `typing.Callable` and
`typing.Iterator`. These are compile-time namespaces, not a general Python import
system. You cannot bring in NumPy or a neighboring application module with an
ordinary import and expect it to compile.

## Keep the useful similarities

Argument evaluation order, list mutation, insertion-ordered dictionaries,
Unicode code-point strings, exception handling, and closure late binding are
deliberately Python-shaped. The compiler rejects many dynamic cases instead of
quietly changing their meaning.

The [language reference](../language/index.md) describes accepted source.
[Runtime compatibility](../reference/compatibility.md) records the remaining
divergences, including float formatting, integer width, and Wasm traps.
