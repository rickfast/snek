# Language reference

Snek keeps Python's surface syntax while making types and supported operations
known before execution. There is no embedded Python interpreter in generated code.

| Topic | Includes |
| --- | --- |
| [Modules and imports](modules.md) | Local files, packages, relative imports, aliases, initialization |
| [Types and numbers](types.md) | Annotations, local inference, machine widths, conversions, optionals |
| [Functions and closures](functions.md) | Defaults, keyword binding, callable values, shared captures |
| [Classes and protocols](classes.md) | Fields, inheritance, initialization, equality, context managers |
| [Exceptions and cleanup](exceptions.md) | Typed handlers, re-raising, `else`, `finally` |
| [Strings and formatting](strings.md) | Code points, Unicode methods, f-strings, literal format specs |
| [Collections](collections.md) | Lists, dictionaries, tuples, live views, keyed sorting |
| [Generators and async](async.md) | Suspension, iteration, explicit close, typed awaits |

## Control flow

`if` / `elif` / `else`, `while`, `for`, `break`, `continue`, `assert`, and recursion
are supported. Conditions must be `bool`; use explicit comparisons or `bool()`
where appropriate. Loops can traverse ranges, strings, lists, dictionaries and
their views, homogeneous tuples, and Snek generators.

The checker tracks definite assignment. An assignment that only occurs on one
branch or inside a loop does not make a local safe to read afterwards.

## Reading this reference

The detailed sections are included directly from the repository's `DESIGN.md`.
They describe language rules and, where useful, how those rules map to Wasm.
They are not a promise of complete Python compatibility: unsupported syntax and
dynamic behavior are rejected. Start with [Coming from Python](../learn/python.md)
for a shorter overview.
