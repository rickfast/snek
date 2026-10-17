# Bootstrapping Snek

Status: **the full source-to-core-Wasm compiler is self-hosted.** Stage 1
compiles the unchanged compiler source into stage 2, and stage 2 compiles that
same source into byte-identical stage 3. Those rebuilds run no Python parser,
checker, IR bridge, emitter or encoder. Python remains the default reference
compiler; select the independent implementation with `--compiler snek`.

## Source layout and preparation

`parser.py`, `declarations.py`, `bodies.py`, and the `emit_*.py`/`machine_*.py`
modules are the production compiler written in Snek. `encode.py` is its binary
encoder. They live here alongside the host build tools for historical reasons;
the directory name does not mean these passes are experimental.

The host-only `linker.py` uses an explicit module/import manifest and Python
tokenization to select definitions and namespace the encoder. It does not
compile user programs, match import text, or search function bodies with regex.
`build_declarations.py`, `build_bodies.py`, and `build_machines.py` also expose
standalone probes of those passes. `body_bridge.py` remains necessary for seed
construction; it is forbidden during subsequent self-hosted stages.

Authored runtime programs live in `snekc/runtime/*.snek`: exceptions, string
methods, and formatting. A small resource loader resolves private
`__unicode_*` identifiers against the compiler's Unicode flag ABI, replacing
only identifier tokens. Function source locations and lazy helper parsing are
preserved. The bootstrap embeds the resulting source fragments as generated
data, so the standalone compiler needs no resource files at runtime.

The pinned, generated Unicode name functions live in `unicode_names.py`, not
inside the handwritten parser. Regenerate or verify them with
`generate_unicode_names.py --write`/`--check` under Python with Unicode 15.1.
The linker embeds this module into both parser probes and the full compiler.
Compiler entry and profiling builds share the phase definitions in
`build_emitter.py`.

## Run the full compiler bootstrap

The `Self-hosting` GitHub Actions workflow runs on pushes, pull requests and
manual dispatches. Separate Node and Wasmtime (Python binding) jobs start from
a clean checkout with no cached compiler, build all three stages, compare stage
2/3/published binaries, and run the compiler-isolation and full core regression
suites. The Wasmtime job also runs all component examples, independent
component/host-I/O tests, and cancellation/repeated-invocation tests using the
Wasmtime CLI. Both raw and optimized builds are required; skipped independent
component tests fail CI. Each job uploads its source bundle, stage binaries and
hash manifest for 14 days.

Tool versions are pinned in `.github/workflows/selfhost.yml`; native component
tool downloads have SHA-256 checks. To reproduce its gates locally, use a fresh
checkout with those tools installed and run the commands below, setting
`SNEK_RUNTIME=node` or `SNEK_RUNTIME=wasmtime-py` for the bootstrap/core gates.
```sh
python bootstrap/bootstrap_compiler.py
python tests/check_selfhost_compiler.py
SNEK_COMPILER=snek python tests/run_tests.py
python tests/check_selfhost_components.py
python -m snekc run examples/closures_suspended.py --compiler snek --no-opt
SNEK_RUNTIME=wasmtime python -m snekc run examples/stdlib_host.py --compiler snek --component
```

For the complete component gates, also run:

```sh
SNEK_COMPILER=snek python tests/check_components.py
python tests/check_selfhost_components.py --require-no-skips
SNEK_COMPILER=snek python tests/check_component_lifecycle.py
```

The bootstrap needs Python to prepare the fixed source bundle and seed, plus a
supported Wasm runtime. No previously built checker, optimizer or external core
assembler is required. It disables host compiler entry points during the two
rebuilds, checks the byte-for-byte fixed point, executes a stage-3-compiled smoke
program, and checks invalid source produces no partial binary. Only then does it
publish `compiler.bundle.py`, `compiler.stage{1,2,3}.wasm`, `compiler.wasm` and
`compiler-manifest.json` into the ignored `bootstrap/build/` directory.

The compiler itself is a standalone WASI command:

```sh
wasmtime run bootstrap/build/compiler.wasm < examples/shapes.py > /tmp/shapes.wasm
wasmtime run /tmp/shapes.wasm
wasmtime run bootstrap/build/compiler.wasm --wat < examples/shapes.py
```

Input is UTF-8 Snek source; output is a core Wasm binary (or folded WAT with
`--wat`). `--component` generates the core guest for native WASI 0.3 linking,
and `--encode` accepts folded WAT instead of source. The Python CLI does file and
runtime orchestration, optional Binaryen optimization, and component packaging
with `component.py`/`wasm-tools`; those tools do not parse/check/lower Snek or emit
its core program. The standalone compiler requires no Python process.

`SNEK_COMPILER=snek` sets the CLI/API default; `SNEK_COMPILER_WASM` selects a
different compiler binary. Selection fails closed. `--frontend` is a separate
reference-compiler switch, not a substitute for `--compiler snek`.

The owned backend consists of `emit_program.py` (typed expressions/statements),
`emit_runtime.py` (collections, keyed operations and byte I/O), `emit_host.py`
(WASI adapters), `emit_module.py` (layouts, protocols and module assembly), and
`emit_suspension.py` (direct connection to the owned CFG/frame passes).
`emit_support.py` supplies literals, types and Unicode tables, and `encode.py`
assembles the core binary. The source linker embeds static runtime/configuration
data and namespaces the encoder; it is not run between bootstrap stages.

The fixed string/formatting runtime library is indexed once during bundle
preparation using the owned parser (not CPython's AST). That step validates the
entire library and embeds each function's raw source, name and starting line;
it does not serialize prechecked bodies. `BodyChecker` initially registers these
lightweight entries and parses a helper only when its signature is first needed.
The resulting AST/signature is cached within that compilation, including for
transitive helper calls. Original source locations and the full runtime ABI hash
are retained. No helper AST is shared across compilations. Eager AST input remains
supported for parity tests; `tests/check_lazy_runtime.py` checks all helper ASTs,
locations, cache behavior and eager/lazy typed-IR equality.

Diagnostic wording differs from the reference checker. The negative corpus
still checks specific messages: `tests/selfhost_diagnostics.json` records the
owned checker's wording where the original fixture's substring differs. No
negative fixture is skipped or reduced to a generic failure assertion.

The sections below retain the narrower pass-level proofs and reference adapters
for development and differential testing. Their Python bridges are not used by
the full compiler.

## Run the backend bootstrap

From the repository root, with Python 3.12+ and a supported Wasm runtime:

```sh
python bootstrap/bootstrap.py
python bootstrap/bootstrap.py --check-examples
python tests/check_bootstrap.py
python tests/check_byte_io.py
```

Use `SNEK_RUNTIME=wasmtime-py`, `wasmtime`, or `node` as with the normal runner.
No Binaryen or `wasm-tools` is needed for these commands. The regular regression
suite still exercises optimized builds and therefore needs Binaryen.

`encode.py` is ordinary, statically typed Snek. Its only imports are future
annotations, integer types and byte I/O from the CPython-compatible `snek` shim.
It has its own WAT reader, symbol resolution, type/instruction/section encoders,
LEB128 routines and exact IEEE-754 literal conversion. The float converter uses
small arbitrary-precision unsigned integers built from `list[i32]` limbs; it does
not call a Python float parser, `struct`, or a host compiler.

The bootstrap script performs these checks:

1. The Python compiler compiles `encode.py` into WAT and produces a seed binary
   with debug-name sections disabled.
2. That seed runs as Wasm and encodes the same WAT into stage 2.
3. Stage 2 runs as Wasm and encodes the WAT into stage 3.
4. All three binaries must be byte-for-byte identical. Stage 3 must also encode
   a separate program that successfully runs and prints the expected output.
5. With `--check-examples`, stage 3 must match the Python encoder for every
   example, both preview1 core output and the core inside a WASI 0.3 component.

Only after verification does the script publish `encoder.stage1.wasm` through
`encoder.stage3.wasm`, `encoder.wat`, and a hash manifest into `bootstrap/build/`
(ignored by Git). The stages are unoptimized and omit debug names, so optimizer
rewrites and custom-section differences cannot mask a failed comparison.

The resulting encoder is a standalone core WASI command. It reads folded WAT
from stdin and writes binary Wasm to stdout, without decoding or re-encoding
the binary as text:

```sh
python -m snekc wat examples/shapes.py |
  wasmtime run bootstrap/build/encoder.stage3.wasm > /tmp/shapes.wasm
wasmtime run /tmp/shapes.wasm
```

This backend accepts the compiler's WAT dialect, not arbitrary WAT. In
particular, it has no flat-instruction parser, active data/table segments, SIMD
or component binary encoder. Identifiers are ASCII, as required by Snek.
Float literals use bounded exponents (±4096 after the radix-point adjustment),
which cover the compiler's machine-float output. Errors exit nonzero on stderr
without publishing a partial binary on stdout. Debug custom sections are omitted.

## Run the source parser

```sh
python -m bootstrap.linker --out bootstrap/build/parser.bundle.py
python -m snekc build bootstrap/build/parser.bundle.py --no-opt -o bootstrap/build/parser.wasm
wasmtime run bootstrap/build/parser.wasm < bootstrap/encode.py
python -m snekc run bootstrap/build/parser.bundle.py --no-opt < bootstrap/parser.py
python tests/check_parser.py
```

The command reads UTF-8 source from stdin and prints a JSON syntax tree. It
needs only the core WASI byte-input and text-output facilities at runtime:
there is no Python process, `ast`, `tokenize`, regex engine, or parser callback.
`python -m bootstrap.parser` runs the same implementation with the CPython shim
for development. Set `SNEK_WASM_OPT` when running the tests to include optimized
self-source parsing; the other parser tests do not need Binaryen.

### Use it in the compiler

```sh
python -m snekc check examples/shapes.py --frontend snek
python -m snekc build examples/shapes.py --frontend snek --no-opt
python -m snekc run examples/named_unicode.py --frontend snek
python -m snekc run examples/async_functions.py --frontend snek --component
python tests/check_frontend.py
SNEK_FRONTEND=snek python tests/run_tests.py
SNEK_FRONTEND=snek python tests/check_components.py
```

`SNEK_FRONTEND=cpython|snek` selects the default for all compiler APIs and CLI
commands; an explicit `frontend=` argument or `--frontend` flag wins. The default
without either is still `cpython`. Run the two regression commands above with
each frontend to exercise both paths (Binaryen and the component tools are
required as usual).

The integrated Snek frontend runs `parser.py` **with the CPython shim**, then
`snekc/frontend.py` adapts its owned tree into the existing checker's `ast` node
interface. The adapter restores `Load`/`Store`/`Del` contexts without reparsing
source, and converts number spellings with Python numeric constructors. All
source parsing uses the selected frontend, including generated built-ins and
quoted type annotations. There is no silent CPython-parser fallback. Python
objects, numeric conversion and the checker/emitter still depend on Python;
this option is an integration step, not a Python-free compiler.

`tests/check_frontend.py` compares exact emitted WAT for both core ABIs, checks
error-message parity, CLI selection and source diagnostics, and compiles with
`ast.parse` disabled to catch fallback. It also **executes the Wasm parser** on
every example and feeds the resulting JSON through the adapter/checker/emitter,
comparing output against the default frontend. The normal CLI does not launch
the Wasm parser or pay an engine-startup cost for each module.

`parser.py` contains a tokenizer, recursive-descent statement parser, expression
precedence parser, string/f-string readers, and a tagged `Node` tree. It covers:

- Logical newlines, indentation/dedentation, tab-consistency checks, comments,
  explicit line continuations, and nested delimiters.
- Functions/classes/decorators, annotations and parameter defaults, lambdas,
  imports, assignments/unpacking, loops, conditionals, exceptions, context
  managers, `async`/`await`, `yield`, and `nonlocal`.
- Calls, attributes, slices, lists/dictionaries/tuples, arithmetic/comparisons,
  boolean/conditional expressions, integer/float literals, and string escapes.
- Adjacent/raw/triple-quoted strings; f-string expressions, conversions, debug
  fields, nested format fields, and Python 3.12-style quote reuse/comments.

This is a **syntax** layer, not a replacement for the checker. For example,
unknown names, duplicate parameters, invalid types, and `break` outside a loop
are checker concerns. It can represent some syntax the checker rejects, such
as starred calls, dynamic format fields, and keyword-only parameters. This
lets later phases issue specific diagnostics without conflating syntax and
type errors.

### AST contract and compatibility

AST objects have an `_type` tag and named fields; arrays, strings, booleans and
null are ordinary JSON values. A numeric constant stores
`{"_type":"Number","text":"0x_FF"}` as its value. Keeping literal spelling
avoids premature rounding or overflow before the typed checker chooses a
machine type. Operators are tagged nodes too. `Node.get()` and `Node.put()` are
the in-language field API; JSON is an inspection/test interface, not a required
handoff between future compiler phases.

Source origins use `_line` (one-based) and `_column` (zero-based UTF-8 byte
offset). F-string expressions retain their original source coordinates; text
fragments use the enclosing string-body origin. These are diagnostic origins,
not a lossless concrete syntax tree: comments, end spans, redundant parentheses,
CPython `ctx`, type comments and literal-prefix metadata are not retained.
Assignment context is represented by the containing statement/target field.

The compatibility target is Snek's supported grammar, **not every future CPython
grammar**. CPython 3.12+ is a development-time differential reference. New Python
syntax is adopted deliberately, with fixtures, rather than automatically when
the host Python version changes. Remaining boundaries are explicit:

- Identifiers are ASCII; source and string values must be valid Unicode scalar
  values. Input is UTF-8 (optional BOM), with CRLF/CR normalized to LF; encoding
  cookies do not select another decoder.
- Bytes/complex literals, sets/comprehensions, assignment expressions, pattern
  matching, exception groups, and type-parameter/type-alias syntax are outside
  this parser's current subset.
- Named Unicode escapes (`\N{...}`) use pinned Unicode 15.1 names and aliases,
  including algorithmic CJK/Hangul names. They are ASCII case-insensitive;
  named sequences are rejected, matching Python string-literal semantics.
  The CPython frontend follows its host Unicode version, so names introduced
  after 15.1 require an explicit table update in the Snek frontend.
- Diagnostics identify a source line and column, but do not reproduce CPython's
  message wording. Delimiter nesting is limited to 200; the parser is not a
  resource-sandboxing boundary for hostile input.

`tests/check_parser.py` compares normalized tree structure and literal values
against CPython for both bootstrap sources, all examples, checker-error fixtures,
benchmark programs, generated built-in source, and focused grammar cases. It
also compares the complete JSON output of the CPython-shim and Wasm versions,
checks malformed syntax/UTF-8 and documented subset rejections, exercises 10,000
deterministic syntax mutations, and checks raw/optimized self-source parity.
Another test uses the **Snek binary encoder** to assemble the parser's WAT,
compares its binary to the Python encoder, and executes it on its own source.
Python still produces that WAT; this is not a full compiler fixed point.

### Reproduce Unicode-name data

The marked block at the end of `parser.py` is generated data, embedded as a
single raw string so the standalone parser needs no filesystem or host lookup.
Sorted explicit names and aliases use binary search; CJK ranges and Hangul
syllables use algorithmic lookup. The table and alias input have SHA-256 checks.
Unicode's license is included in `data/UNICODE-LICENSE.txt` and in distributions.

Regeneration is offline and requires `unicodedata.unidata_version == "15.1.0"`
(e.g. CPython 3.13). Normal builds use the checked-in block and do not regenerate
it or require that host Unicode version:

```sh
python bootstrap/generate_unicode_names.py --check
python bootstrap/generate_unicode_names.py --write
```

Canonical names come from the pinned CPython UCD. Formal aliases come from the
vendored [Unicode 15.1 NameAliases.txt](https://www.unicode.org/Public/15.1.0/ucd/NameAliases.txt),
retrieved from the [Unicode project's versioned mirror](https://github.com/unicode-org/unicodetools/blob/main/unicodetools/data/ucd/15.1.0/NameAliases.txt).
The generator rejects a changed alias-file hash or a different UCD version
rather than silently changing language behavior. Tests cover every name/alias
against the pinned host, plus a compiled-Wasm sample including every alias.
Other runtime Unicode property/case tables still use the existing compiler's
host-derived tables; pinning those belongs to the emitter/data port.

## Run the declaration/type pass

```sh
python bootstrap/build_declarations.py
wasmtime run bootstrap/build/declarations.wasm < examples/shapes.py
python tests/check_declarations.py
SNEK_RUNTIME=node python tests/check_declarations.py
```

`bootstrap/declarations.py` consumes the parser's `Node` tree directly. It owns
the type, parameter, signature, field, method and class records; it does not
convert them into Python `ast` or call the Python checker. It resolves:

- Module names/imports, including the static standard-library namespaces.
- Forward class references, single inheritance, cycle/final checks, inherited
  fields and constructors, and stable virtual-method slots and overrides.
- Numeric/reference/container types, optional references, class objects,
  callables, generator/coroutine signatures and quoted annotations.
- Parameter defaults, field declarations, special-method signatures and the
  built-in exception hierarchy. Integer defaults are range-checked without
  overflowing an intermediate signed value. Override comparisons use exact
  binary64 rounding for float tokens, reusing the Snek encoder's limb helpers.

The build script makes a **fixed source bundle**, not a general module loader:
it joins the parser, this pass and the required numeric functions, and embeds
the existing built-in source, reserved helper names and library export registry
as build-time data. Those registry inputs remain Python-owned for now. The seed
is built by the Python compiler. Before publishing `declarations.bundle.py` and
`declarations.wasm` into `bootstrap/build/`, the script executes the Wasm pass
on its own bundled source and requires exact JSON parity with the shim.

The standalone command reads UTF-8 source and prints versioned declaration IR.
Function/method bodies and module statements remain owned syntax trees in the
in-memory model; JSON reports signatures/layouts and the main-statement count,
not body IR. It explicitly reports `body_checking: "pending"`. **A successful
declaration pass is not a successful program type check.** The separate
body pass below implements local inference, name-use resolution, definite
assignment, optional-reference narrowing and expression/statement IR for its
explicit subset, including collections, constructors/methods, string helpers,
formatting, exceptions, context managers, closures, generator/async bodies,
standard-library calls and keyed sorting/reductions. The production `check`, `build`
and `run` commands are unchanged.

`tests/check_declarations.py` compares complete declaration metadata against
the Python checker (including constructor signatures) for every example,
benchmark, both earlier bootstrap sources and the new bundle. It runs the same
inputs through compiled Wasm, covers declaration-stage error fixtures and
focused failures, and disables Python parsing/checking/AST conversion to detect
fallback. Set `SNEK_WASM_OPT` to also test an optimized binary on the focused
corpus and its own source. These checks pass on Wasmtime and Node; they do not
claim function-body or full-compiler fixed-point coverage.

Annotation nesting is capped at 200. Float default *override comparisons* have
a temporary limit of 3000 significant decimal digits; ordinary source float
tokens retain their spelling. Diagnostics preserve source origins, but default
type-error wording need not match the Python checker. Duplicate parameters are
rejected in this pass rather than waiting for the body/scope pass.

## Run the experimental body checker

```sh
python bootstrap/build_bodies.py
wasmtime run bootstrap/build/bodies.wasm < examples/list_builtins.py
python bootstrap/body_bridge.py examples/list_builtins.py \
  --checker-wasm bootstrap/build/bodies.wasm --no-opt -o /tmp/ordered-checker.wasm --run
python tests/check_ordered_bodies.py
SNEK_RUNTIME=node python tests/check_ordered_bodies.py
```

`bootstrap/bodies.py` consumes the declaration model and owned source AST. It
checks all top-level functions, constructors/methods, required runtime helpers
and the module's main body before emitting a `BodyModule` with `schema: 7`,
`subset: "language-v7"` and `body_checking: "complete-for-subset"`. The nested declaration record still
describes the declaration-only phase. Unsupported constructs fail explicitly;
there is no fallback to the Python checker and no partial IR printed on error.

This slice supports:

- `i32`, `i64`, `f32`, `f64`, `bool`, `str`, reference optionals, and `None` returns.
  Numeric literals carry resolved types and exact integer text or binary64 bits;
  implicit widening and explicit conversions become IR nodes.
- Scope-wide local annotations, inferred locals, definite assignment, strict
  boolean conditions, optional-reference narrowing through branches/short-circuit
  expressions/assertions, and conservative loop flow.
- Arithmetic/comparisons, conditional expressions and mixed-int/float guards;
  direct/recursive calls, positional/keyword/default arguments, returns,
  assignments, `if`, `while`, `range` loops, `break` and `continue`.
- String literals, concatenation, indexing, comparisons, `len`, `str`, `repr`,
  `ord`, `chr`, scalar conversions, `abs`, two-argument `min`/`max`, and `print`.
  Argument expressions retain source evaluation order, including reordered
  keywords; print arguments are evaluated before any of that print's output.
- String methods backed by the existing Snek runtime source, including Unicode
  casing and classification, searching, splitting, joining and replacement.
  `i32(str)` and `i64(str)` support Unicode decimal digits and checked overflow.
  Helpers and their dependencies are checked on demand through this same Snek
  body pass; private buffer types and runtime intrinsics stay inaccessible to
  user source. Unicode property/case/decimal requirements travel in the IR.
- Assertions with lazy messages, built-in and user exception classes, raises
  and re-raises, typed/tuple/bare handlers, `else` and `finally`. Handler bindings
  are deleted after use, and re-raises retain the original caught exception
  even when its source binding is reassigned. Cleanup runs across returns,
  breaks, continues and exceptions; `return`, `break` and `continue` cannot exit
  a `finally` block.
  Exception string overrides can delegate to `super().__str__()`.
- Typed lists, fixed-length tuples and dictionaries, including nested and
  optional elements, tuple unpacking, indexing, list/string slicing, mutation,
  common collection methods, membership, equality and representable printing.
  Lists support concatenation and repetition (including alias-preserving
  augmented assignment); dictionaries support live views and key deletion.
- Iteration over lists, homogeneous tuples, Unicode strings, dictionaries and
  their views. Dictionary iteration retains the runtime's mutation checks and
  emits internal cleanup on `break`, `return` and exceptional exits.
- `sorted`, `sum`, and list-based `min`/`max`, including literal `key=None`,
  boolean `reverse`, eager defaults and numeric starts. Empty lists use the
  same contextual inference and float-start restrictions as the production
  checker. Resolved list-operation IR preserves source argument order, stable
  sorting, first-extremum identity, integer widening and compensated float sums
  through the existing emitter. Callable keys support named functions and
  context-inferred lambdas, cached-key sorting and source-order evaluation.
- Imported `read_bytes()` and `write_bytes(list[i32])` on the core WASI target.
  The checker resolves their signatures to ordinary runtime calls. The existing
  byte-stream runtime preserves binary stdin/stdout, flushes text in order,
  rejects invalid byte values before writing, and handles short host I/O.
  These calls remain explicitly unavailable for component output.
- Ordinary class constructors with two-phase field initialization, `Final`
  enforcement, inherited constructors, inlined `super().__init__`, field reads
  and writes, virtual/overridden methods and direct `super()` method calls.
  Owned class types support upcasts, common-base inference, identity,
  `isinstance` narrowing, `__str__`, `__len__` and checked `__eq__` dispatch.
  Explicit constructor returns preserve two-phase initialization and return
  from an inlined superclass body without exiting the subclass constructor.
- F-strings with static format specifications, `!s` and `!r`. `formats.py`
  validates the grammar and flag/type combinations without a regex engine or
  Python's `format()`. Formatting helpers, including exact float rounding, are
  demand-checked through the Snek body pass.
- Tuple concatenation, fixed-length repetition and literal-bound slicing;
  class objects, dynamic class names and `type[T]`; context managers lowered
  to explicit exception/cleanup IR with conservative suppression flow.
- First-class callables, nested functions/lambdas, recursive and transitive
  captures, `nonlocal`, shared cells and exception-binding cleanup.
  `scopes.py` owns lexical analysis; cell/environment lowering runs in Snek.
- Generators, generator methods/iteration, coroutine bodies, `await`, `run`
  and `checkpoint`, plus the existing static `sys`, `os`, `time` and `secrets`
  APIs. The checker emits suspension IR. The checker-only bridge uses the
  reference emitter or the pass-level Snek executables below; the full compiler
  uses the owned CFG/frame emitter and host-runtime generators directly.

Float parsing from strings remains unsupported, as in the production compiler.
As in the production
compiler, these sorting/reduction builtins take lists, not arbitrary iterables.
Constructor fields must be initialized before the first use of `self`;
conditional field initialization is not supported. Range steps must be nonzero
integer literals; integers must fit a signed machine width. Float conversion reuses the current
3000-significant-digit bootstrap limit. Diagnostics are not wording-compatible
with the Python checker.

The standalone checker runs entirely in Wasm. **Code emission is still Python:**
`bootstrap/body_bridge.py` converts resolved records to the existing Python IR
dataclasses, supplies the current built-in exception allocation ABI, and calls
the existing emitter/encoder. Its runtime ABI bridge checks the built-in source
contract and a fingerprint of the helper source, intrinsic signatures and
Unicode-use ABI; stale checker artifacts must be rebuilt. It neither
reparses user source into Python AST nor invokes the Python checker. Without
`--checker-wasm`, the same Snek checker runs through its CPython shim. Output is
published only after checking/assembly succeeds. This remains an experimental
command, not a new `snekc` frontend/checker selection.

The build script verifies exact shim/Wasm IR parity for a recursive program using
a class, list, dictionary, string methods, integer parsing, exception handling,
sorting and numeric reductions,
and executes the binary emitted from that IR before publishing `bodies.bundle.py`
and `bodies.wasm`. The test suite exercises raw/optimized checkers and programs,
the Fibonacci, Mandelbrot and list-builtins benchmarks, scalar/aggregate/runtime/error/evaluation-order
fixtures, CLI failure preservation, explicit unsupported-feature errors, and disabled
Python parsing/checking/AST-adapter fallbacks. All existing negative fixtures
must fail closed, but the tests distinguish semantic errors from not-yet-ported
features. Shared runtime divergences (notably float repr, surrogate `chr`,
machine-width integer overflow and NaN sorting) are compared exactly against
the existing compiler rather than treated as new checker regressions.
A focused component-core module is also validated; this
is not yet full component-suite coverage.

`tests/check_aggregate_bodies.py` includes the scalar regression suite, plus
class layout/vtable parity, collection mutation and evaluation order, inherited
constructor binding, dictionary iteration cleanup, optimized-checker IR parity,
and aggregate component-core validation. `tests/check_runtime_bodies.py` extends
these to 29 test groups with string-method/Unicode parity, helper privacy and
dependency checks, exception-flow errors, nested cleanup, user exceptions,
optimized-checker IR parity and runtime component-core validation.
`tests/check_ordered_bodies.py` extends these to 38 groups with ordered builtin
inference/errors, source-order mutation, stable sorting, numeric edge cases,
generated inputs, resolved IR types, optimized parity and component-core checks.
Set `SNEK_WASM_OPT` to include optimized checks. The former fixed-input roundtrip
now checks the unchanged full bundle and runs the rebuilt checker on fresh input.

## Rebuild the parser/checker from Snek source

```sh
python bootstrap/bootstrap_checker.py
python tests/check_selfhost_bodies.py
SNEK_RUNTIME=node python tests/check_selfhost_bodies.py
```

The bootstrap command needs Python and a supported core Wasm runtime, but no
optimizer. It performs these checks before publishing artifacts:

1. Build the seed with the production Python compiler and the Snek frontend.
2. Run the seed on the **unchanged Snek source bundle**, require exact typed-IR
   parity with the Snek shim, then use the Python bridge/emitter/encoder to build
   stage 2 from that resolved IR. No Python body checking or AST adapter is used.
3. Run stage 2 on the same source and emit stage 3 through the same bridge.
   Require stage 2 and stage 3 binaries to be byte-for-byte identical.
4. Run stage 3 on itself and every existing example, comparing complete owned
   IR with the shim. Check diagnostics with no partial IR on errors,
   and emit/run a checked program that must print `55`.

The verified artifacts are `checker.stage1.wasm`, `checker.stage2.wasm`,
`checker.stage3.wasm`, `bodies.bundle.py`, and `checker-manifest.json` under
`bootstrap/build/`. The manifest records source/runtime ABI and stage hashes.
`bodies.wasm` becomes a copy of verified stage 3 for the experimental bridge CLI.
The seed is not required to match later binaries: it uses a different checker
and retains debug names. Stages 2 and 3 omit debug names and use no optimization.
An ordinary `build_bodies.py` run replaces `bodies.wasm` with a fresh seed; run
`bootstrap_checker.py` again to publish a verified rebuilt checker.

`tests/check_selfhost_bodies.py` runs all earlier body regressions against stage 3,
not just the seed. Its 46 groups also cover exact rebuild hashes, binary CLI transport
and EOF, invalid bytes, short/failing WASI calls, import/signature rules, explicit
component rejection, malformed input, optimized stage-3 self-checking, and
preservation of old artifacts when a bootstrap proof fails. Python parsing,
checking and the AST adapter are disabled while testing the rebuild path.

`tests/check_language_bodies.py` extends that suite with formatting validation
and output differentials, tuple slicing/effect checks, constructor/context-manager
examples, closure conversion invariants, callable-key evaluation, generators and
async examples, standard-library binary streams, and expanded component-core
validation. Its closure tests also disable Python closure lowering to catch
fallback. Run it with `SNEK_RUNTIME=wasmtime-py` and `SNEK_RUNTIME=node`;
set `SNEK_WASM_OPT` to include optimized builds.

`SNEK_RUNTIME=wasmtime python tests/check_language_components.py` runs the real
CLI/component host suite using the published rebuilt checker, with Python
source parsing and body checking disabled for guest compilation. It covers
incremental reads and retained lookahead, bounded reads before EOF, stream
aliases/fields/closures and cleanup, host errors, cancellation, repeated
invocations and resource reset, exact binary transport, and signature failures.
This needs the Wasmtime CLI and wasm-tools. `SNEK_WASM_OPT` enables raw/optimized
coverage. The Python emitter remains the explicit downstream bridge.

The stream ABI uses real `io.TextInput`, `io.TextOutput`, `io.BinaryInput`, and
`io.BinaryOutput` values, including imported type aliases, optional narrowing,
fields, closures, `.buffer`, `.closed`, close/flush/drain, sized binary reads and
`readline`, and `with` cleanup. Schema 7 rejects the earlier static stream-call
ABI rather than silently linking it against the new runtime.

This checker-only proof is narrower than the full compiler proof above: its
emission bridge deliberately remains Python so the checker can be tested alone.

## Owned emitter foundations

`emit_support.py` ports monomorphization names, Wasm value/storage types,
transitive collection registration, collection/closure/class layouts, vtables,
UTF-8 WAT literal pooling, and Unicode table generation into Snek.
`build_emitter.py` links these with the parser/checker and the complete backend;
`bootstrap_compiler.py` publishes the verified standalone compiler separately
from the narrower checker artifacts.

Unicode generation uses the compiler's own string runtime. The seed compiler's
Unicode version is inherited through the embedded property/case/decimal tables;
subsequent generation needs neither Python `unicodedata` nor an external UCD
installation. Surrogates, which cannot inhabit Snek strings, have explicit Cs
property defaults. Table run merging, case-closure expansion, final sigma,
special mappings and decimal-zero offsets preserve the existing ABI.

```bash
python tests/check_emitter_support.py
```

This checks every code point's property flags and compares exact WAT output
for full/restricted Unicode tables, escaped literals and the prelude, class
hierarchies, vtables, and nested collection/callable types. Wasm probes are built
from source checked by the rebuilt Snek checker; their output is generated
inside Wasm with the host emitter/Unicode entry points disabled. Set
`SNEK_WASM_OPT` to test raw and optimized probes, and run under both Node and
Wasmtime. The full compiler integrates these foundations with ordinary emission,
collection helpers and module/runtime assembly.

## Owned suspension backend

`machine_lower.py` implements await normalization and suspension control-flow
graphs in Snek: lazy branches, operand capture, loop edges, exception dispatch,
and finally suites on normal, return, break, continue and exceptional exits.
`machine_emit.py` generates frame layouts and factories, local save/restore,
resume dispatch and exception injection, reference clearing, and the
resume/advance/claim/next/throw/close protocol helpers in Snek. These are
executable compiler passes, not just output probes. Build them with the
published owned checker and opt in for either compiler frontend or the owned
body bridge:

```bash
python bootstrap/build_machines.py
export SNEK_MACHINE_LOWERER="$PWD/bootstrap/build/machines.wasm"
export SNEK_MACHINE_EMITTER="$PWD/bootstrap/build/machines.wasm"
python -m snekc run examples/async_order.py
python bootstrap/body_bridge.py examples/async_order.py --checker-wasm bootstrap/build/bodies.wasm -o /tmp/async-order.wasm --run
# Reference/parity tests select the passes themselves:
env -u SNEK_MACHINE_LOWERER -u SNEK_MACHINE_EMITTER python tests/check_machine_lowering.py
SNEK_RUNTIME=wasmtime python tests/check_language_components.py
```

The versioned wire adapter carries checked IR and opaque type/declaration
handles without doing control-flow lowering. Every machine, including generated
checkpoint, stream and sleep helpers, goes through the selected pass. Missing or
failing selected binaries are errors; there is no Python CFG/frame/helper
fallback. The selectors are independent: either pass can be compared against
the Python reference. The emitter adapter still supplies ordinary expression
and statement WAT fragments, resolved type/storage names, temporary declarations,
and literal references. It does not generate suspension dispatch or protocol
WAT. Inline machine-operation calls/accessors remain part of ordinary expression
emission. The adapter currently invokes Wasm separately for each graph and frame,
plus module-wide machine layouts and helpers; batching is a future compile-time
optimization.

Tests compare exact graphs and core/component WAT across all examples, execute
async/generator/closure/cleanup and stream cases using raw and optimized lowerers,
check operand order, malformed transport, and scalar/reference/optional machine
layout and protocol variants. Python CFG, frame, layout and protocol emission
are disabled on the selected path. Run under both Node and Wasmtime; `SNEK_WASM_OPT` enables
optimized coverage. The component suite additionally disables Python source
parsing/checking and exercises cancellation and resource reset with the selected
backend. This pass-level adapter keeps ordinary emission in Python for parity
testing. The full compiler calls both passes directly from Snek through
`emit_suspension.py`, with owned expression/statement emission and no wire bridge.

## Completed stages and acceptance criteria

| Stage | Work | Proof required |
|---|---|---|
| Binary backend | Done: Snek WAT reader and binary encoder, binary stdio | Three identical backend stages; example binary parity |
| Source frontend | Implemented: standalone parser, pinned Unicode-name data and opt-in compiler integration through a temporary AST adapter | Encoder/parser self-source, AST/WAT and error parity, malformed-input tests, core/component regression suites |
| Checker and IR | Standalone parser/checker covers all existing examples, including formatting, constructor returns, context managers, callable keys, closure conversion and suspension IR; stage-2/stage-3 fixed point uses the Python emitter bridge | Positive/error execution suites, owned IR parity and no Python checking/closure-lowering fallback |
| Emitter and data | Done: ordinary expressions/statements, collections, host helpers, protocols, layouts, literals, Unicode, suspension and module assembly execute in Snek | Core and component behavior matches the reference compiler |
| Full compiler bootstrap | Done: seed compiler builds a compiler that consumes its own **Snek source**, then rebuilds it again | Stage 2 and stage 3 binaries match; source/execution/error suites and guarded component-host suites pass |

The backend fixed point starts from WAT; the checker proof retains Python
emission. The full compiler proof starts from Snek source and requires neither.
The Python implementation remains available as the default/reference compiler.
