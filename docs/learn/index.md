# Start here

Snek is for programs that benefit from static types and a WebAssembly target,
while keeping Python-shaped source. You annotate function signatures and class
fields; local types are usually inferred.

1. [Install from source](installation.md). Start with the Python development CLI,
   or build the standalone executable.
2. [Write your first program](first-program.md). Check, run, and compile the same source.
3. [Build a word counter](word-counter.md). Use Unicode strings, dictionaries, and sorting.
4. [Understand the Python differences](python.md). Learn the boundaries before porting a program.

## Which compiler should I use?

| Route | Good for | What you need |
| --- | --- | --- |
| Python development CLI | Learning, inspecting WAT, optimized output, components | Python; a Wasm runtime to execute output |
| Self-hosted compiler through the development CLI | Exercising the independent Snek compiler | A verified compiler bootstrap and a runtime |
| Standalone `snek` executable | Running the compiler without external tools | A locally built native binary |

These routes implement the same language. The development CLI defaults to the
Python reference compiler; `--compiler snek` explicitly selects the self-hosted one.
The standalone executable always uses the embedded self-hosted compiler.

## File names

Use `.snek` for Snek-only programs. Existing `.py` examples work too: both CLIs
read the file contents without requiring a particular extension. GitHub highlights
`.snek` files as Python through the repository's `.gitattributes`.

The reference compiler, Python tooling, and dual-use bootstrap sources keep
`.py` names because CPython imports and executes them. A file extension does not
change the language's type rules.
