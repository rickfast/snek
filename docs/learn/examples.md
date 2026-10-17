# Examples

For a multi-file application, run this from the checkout:

```sh
python -m snekc run examples/modules/main.py --no-opt
```

Its `geometry` package demonstrates classes,
relative imports and re-exports; the unchanged `.py` sources also run under
CPython with the Snek shim. See [modules and imports](../language/modules.md).

The repository's examples are executable regression fixtures. Most can be run
with either compiler, and the core/component corpus compares supported behavior
against CPython.

```sh
python -m snekc run examples/shapes.py --no-opt
python -m snekc run examples/shapes.py --compiler snek --no-opt
```

The second command requires the [full compiler bootstrap](../internals/self-hosting.md).

| Explore | Source | What to look for |
| --- | --- | --- |
| Classes | [shapes.py](https://github.com/rickfast/snek/blob/main/examples/shapes.py) | Fields, inheritance, virtual dispatch, narrowing |
| Strings | [str_methods.py](https://github.com/rickfast/snek/blob/main/examples/str_methods.py) | Unicode operations and string methods |
| Formatting | [format_specs.py](https://github.com/rickfast/snek/blob/main/examples/format_specs.py) | Literal format specifications |
| Dictionaries | [dicts.py](https://github.com/rickfast/snek/blob/main/examples/dicts.py) | Typed keys, values, and live views |
| Closures | [closures.py](https://github.com/rickfast/snek/blob/main/examples/closures.py) | Captured cells and function values |
| Cleanup | [context_cleanup.py](https://github.com/rickfast/snek/blob/main/examples/context_cleanup.py) | Context-manager exit paths |
| Generators | [generators.py](https://github.com/rickfast/snek/blob/main/examples/generators.py) | Yield, throw, exhaustion, and explicit close |
| Async | [async_functions.py](https://github.com/rickfast/snek/blob/main/examples/async_functions.py) | Typed awaits and checkpoints |
| Host services | [stdlib_host.py](https://github.com/rickfast/snek/blob/main/examples/stdlib_host.py) | Arguments, environment, clocks, timers, randomness |
| Streams | [stdlib_streams.py](https://github.com/rickfast/snek/blob/main/examples/stdlib_streams.py) | First-class typed stream views |

Tutorial sources are downloadable as [hello.snek](../examples/hello.snek),
[word-count.snek](../examples/word-count.snek), and [streams.snek](../examples/streams.snek).
Those exact files are included in the pages and checked during the docs build.
