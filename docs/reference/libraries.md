# JavaScript libraries

Compile selected Snek functions into a library callable from Node or a browser:

```sh
snek build examples/library/greetings.py --library --export greet --export add --export twice
```

This writes `greetings.wasm`, `greetings.js` and `greetings.d.ts` beside the source.
The JavaScript file is an ES module. In Node projects, use `"type": "module"` in
`package.json`. The Wasm engine needs WasmGC and exception handling, just as for
[command programs](compatibility.md).

The development CLI has the same build flags. It also supports optimization and
`--keep-wat`:

```sh
python -m snekc build examples/library/greetings.py --library --export greet --export add --export twice --compiler snek --no-opt
```

Choose `--compiler python` for the reference compiler. Both use the same bridge
ABI and binding generator, written in Snek. The standalone CLI needs no external
compiler, JavaScript runtime, or Wasm tools to produce the three files.

## Node

```javascript
import { readFile } from "node:fs/promises";
import { instantiate } from "./greetings.js";

const lib = await instantiate(await readFile(new URL("./greetings.wasm", import.meta.url)));
console.log(lib.greet("Snek 🐍")); // Hello, Snek 🐍!
console.log(lib.add(20, 22));     // 42
console.log(lib.twice(9007199254740993n)); // exact i64 arithmetic
```

Run the checked-in example with `node examples/library/node.mjs` after building.
Node callers should pass bytes or a compiled `WebAssembly.Module`; the URL loader
uses `fetch`, which does not load local `file:` URLs in Node.

## Browser

See [browser support](browsers.md) for compatibility checks, error handling,
Web Workers, [Vite/TypeScript integration](browsers.md#bundlers-vite-and-typescript),
and the browser-engine test matrix.

```javascript
import { instantiate } from "./greetings.js";

const lib = await instantiate(new URL("./greetings.wasm", import.meta.url));
document.querySelector("#result").textContent = lib.greet("browser");
```

Serve the files over HTTP. `instantiate` also accepts a `Response`, `ArrayBuffer`,
typed-array view, or compiled `WebAssembly.Module`. Loading is asynchronous;
ordinary Snek exports are synchronous and async exports return Promises. It does
not require a WASI installation.

The checked-in `examples/library/browser.html` runs the same example. After
building, serve `examples/library/` with a static HTTP server and open that page.

## Calling JavaScript from Snek

Declare synchronous host functions with `@host("name")`. The name selects a
function supplied by the application; Snek does not automatically gain access to
browser globals or Node APIs.

```python
from snek import host, i32

@host("get_name")
def get_name() -> str:
    pass

@host("render")
def render(text: str) -> None:
    pass

def increment(count: i32) -> i32:
    count += 1
    render("Hello, " + get_name() + "! Clicks: " + str(count))
    return count
```

Build the checked-in example:

```sh
snek build examples/library/host_demo.py --library --export increment
```

Supply the required functions when loading it:

```javascript
const lib = await instantiate(new URL("./host_demo.wasm", import.meta.url), {
  imports: {
    get_name: () => document.querySelector("#name").value,
    render: text => { document.querySelector("#result").textContent = text; },
  },
});
let count = 0;
button.addEventListener("click", () => { count = lib.increment(count); });
```

Serve `examples/library/` and open `host.html` for a runnable version. JavaScript
owns the DOM and event handlers; Snek receives strings and calls typed functions.
The same imports mechanism works in Node and Web Workers with functions appropriate
to those environments.

Host declarations must be top-level functions (`def` or `async def`), with an optional
docstring followed by `pass`. Names are nonempty ASCII identifiers. Parameters
and return values use the boundary types below; defaults, additional decorators,
methods and nested declarations are rejected. Declarations can live in imported
local modules and be used as callable values inside Snek. Repeated host names must
have identical parameter and return types and agree on sync/async behavior. They
require `--library`, even if unused.

The generated `SnekImports` interface describes required callbacks, and
`instantiate` requires options when a program declares imports. Every declaration
must have a matching own function property, including unused declarations. The
loader snapshots the functions before initialization and calls them without a
receiver; use an arrow function or `.bind()` for methods that need `this`.

Synchronous callbacks receive decoded JavaScript values. Their return types and integer ranges
are checked, including `undefined` for a `None` return. `None` callbacks use
`undefined` rather than `void` in TypeScript so async callbacks are not accepted.
Promises and thenables are rejected at runtime, including for `None` callbacks.
Use an async host declaration for `fetch`, timers and other Promise-based operations.

Synchronous host functions can run during module initialization. A JavaScript exception aborts
the current call (or rejects initialization) and propagates unchanged; it is not
a Snek exception and does not run Snek `except`/`finally` cleanup. Use typed return
values for expected host failures. Boundary validation errors behave the same way.
The JavaScript bridge still resets its scratch memory, and subsequent calls work.
Callbacks cannot invoke exports or Snek callbacks on the same active instance;
a later event can make a fresh call. The primitive [list handle operations](#snek-owned-list-handles)
are safe inside host callbacks.

The CPython shim preserves declarations for imports and tooling but raises a clear
error when called: CPython has no JavaScript host. Python tests can replace the
declared functions with their own test implementations.

## Async imports and exports

Use `async def` with the same decorator to declare a Promise-returning host
function. Await it from a Snek async function:

```python
from snek import host

@host("fetch_text")
async def fetch_text(url: str) -> str:
    pass

async def load(url: str) -> str:
    try:
        return (await fetch_text(url)).strip().upper()
    except RuntimeError as error:
        return "Could not load: " + str(error)
```

```sh
snek build examples/library/async_demo.py --library --export load
```

```javascript
import { instantiate } from "./async_demo.js";

const lib = await instantiate(new URL("./async_demo.wasm", import.meta.url), {
  imports: {
    fetch_text: async url => {
      const response = await fetch(url);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return response.text();
    },
  },
});
const text = await lib.load("./message.txt");
```

Serve `examples/library/` and open `async.html` for a runnable fetch example.
Async exports return `Promise<T>`; async host declarations require
`PromiseLike<T>` callbacks (`PromiseLike<void>` for `None`). Returning a bare
value instead of a Promise/thenable is an error. Resolved values use the same
boundary checks as synchronous calls. Merely creating a Snek coroutine does
not invoke the callback: it starts when that coroutine is awaited.

The loader advances the existing GC-backed coroutine frames until they suspend,
awaits the host Promise, and resumes with the resolved value. No Wasm stack or
borrowed string buffer is retained across the wait. `await checkpoint()` yields
to the JavaScript event loop; CPU-bound work between awaits still runs on the
calling thread, so use a Worker when needed.

Promise rejections, synchronous throws from async callbacks, and invalid resolved
values become catchable Snek `RuntimeError`s with the host name and an error
message. Snek `except` and `finally` run, including awaited cleanup. An uncaught
exception rejects the exported Promise with `SnekError`. The original JavaScript
error object/stack is not preserved. This differs from synchronous host callback
errors, which still escape unchanged and bypass Snek cleanup.

### Concurrent calls

Async exports can overlap on the same instance, including repeated calls to the
same export. Each invocation owns its coroutine frame and pending host result:

```javascript
const [first, second] = await Promise.all([
  lib.load("./first.txt"),
  lib.load("./second.txt"),
]);
```

Both host requests start without waiting for the other to complete. Results may
settle in either order; validation failures and exceptions belong to the invocation
that produced them. `Promise.all` rejects when one call fails, but does not cancel
the others. Each call still runs its own Snek cleanup.

Execution is cooperative, not parallel: only one synchronous step runs at a time.
Other exports and Snek callbacks may run while an invocation is suspended, so
shared captured state can change across an `await`. Synchronous re-entry from a
host function or stdout handler is still rejected. Scratch memory is reset between
steps; no borrowed string buffers survive suspension.

Async module initialization and detached task spawning are not supported. `run()`
is a command entry mechanism, not a way to block JavaScript waiting for a Promise.
Start library coroutines through an async export. An unsettled Promise retains
its invocation's frame until the host settles it or the invocation is cancelled,
but does not block other calls.

### Cancelling an async export

Async exports accept an optional final `{signal}` argument after their Snek
parameters:

```javascript
import {instantiate, SnekCancelledError} from "./async_demo.js";

const controller = new AbortController();
const pending = lib.load("./message.txt", {signal: controller.signal});
controller.abort("user stopped loading");
try {
  await pending;
} catch (error) {
  if (!(error instanceof SnekCancelledError)) throw error;
}
```

An already-aborted signal rejects before starting the coroutine. Otherwise the
driver wakes a suspended call and injects `GeneratorExit` at its next resume,
including through `wait_all` and `gather`. `finally` blocks can await cleanup. The
Promise rejects with `SnekCancelledError` only after cleanup completes; its `cause`
is the signal's reason. A cleanup failure instead propagates as its normal Snek
or JavaScript error. Once cancellation is requested, suppressing `GeneratorExit`
inside Snek does not turn the exported Promise into a success.

Cancellation is cooperative: it cannot interrupt a running synchronous step.
Cleanup that never completes keeps the call pending. Aborting after completion
does nothing, and unrelated calls on the same instance are unaffected. Listeners
are removed when the invocation finishes.

This cancels the Snek invocation, not automatically its JavaScript operations.
Pass the same signal to `fetch` in your host implementation, or abort resources
from a cleanup host function, when the underlying work must also stop. Detached
host results are ignored, and late rejections remain handled. Call options apply
to exported async functions, not synchronous exports or arbitrary callback values.

### Structured concurrency with `wait_all`

Snek can start sibling coroutines within one invocation:

```python
from snek import host, wait_all

@host("fetch_and_render")
async def fetch_and_render(url: str) -> None:
    pass

async def load_both() -> None:
    await wait_all(fetch_and_render("./first.txt"), fetch_and_render("./second.txt"))
```

`wait_all` takes one or more distinct, unawaited coroutines. Calling it creates a
coroutine; awaiting it claims and starts the children. Children run cooperatively
in argument order and may finish in any order. Their return values are discarded,
and the group returns `None` after every child finishes. Nested groups and async
callbacks work too. Duplicate or already-awaited coroutines raise `RuntimeError`
before any child starts.

On the first child failure, unfinished siblings are unwound with `GeneratorExit`.
Their `finally` blocks may await cleanup; the group waits for that cleanup before
re-raising the failure that escaped the first failing child. Sibling cleanup
failures do not replace it; a child's own `finally` still follows normal exception
replacement rules. Use
`finally` for cleanup rather than catching and suppressing cancellation. A child
that never yields can block the whole instance, and cleanup that never finishes
keeps the group pending.

Unwinding a host wait detaches its result, but does not abort the underlying
JavaScript operation. Late resolutions/rejections are handled and ignored. Use
an application-owned `AbortController` in your cleanup host function when the
operation itself needs to be stopped.

This first structured-concurrency API supports core commands and JavaScript
libraries. Core WASI I/O remains blocking; actual overlapping host I/O requires
the library target. Component builds reject `wait_all` until their host-service
state supports multiple pending operations. The CPython shim uses asyncio tasks
and asyncio cancellation for sibling cleanup.

### Collecting results with `gather`

Use `await gather(...)` when you need the children's results:

```python
from snek import gather

async def load_both() -> str:
    texts = await gather(fetch_text("./first.txt"), fetch_text("./second.txt"))
    return "\n".join(texts)
```

Here `fetch_text` is an async host declaration returning `str`, and `texts` is a
`list[str]`. Results stay in argument order even when requests finish out of order.
The returned list is an ordinary Snek-owned list. If its elements are scalars,
strings, object handles or supported nested lists, an exported async function
can return it directly as a [list handle](#snek-owned-list-handles). Other element
types stay inside Snek.

`gather` requires one or more distinct, fresh coroutines with exactly matching,
non-`None` result types. Numbers, strings, objects, optional references, collections
and callables work, as do nested gathers. Mixed result types and `None` results
are rejected at compile time; use `wait_all` when discarding results. There is no
implicit numeric widening or heterogeneous result union.

Failure and cleanup rules match `wait_all`: the first failure escaping a child
unwinds unfinished siblings, awaits their cleanup, then propagates. Unlike
`asyncio.gather`'s default behavior, siblings are not left running after failure.
The CPython shim follows Snek's structured behavior. Component builds currently
reject `gather`, just as they reject `wait_all`.

## Boundary types

| Snek | JavaScript / TypeScript |
| --- | --- |
| `i32` | `number`, checked as a signed 32-bit integer |
| `i64` | `bigint`, checked as a signed 64-bit integer |
| `f32` | `number`, rounded to float32 by Wasm |
| `f64` | `number` |
| `bool` | `boolean` |
| `str` | `string`, copied as UTF-8 |
| `HostObject` | `object`, opaque GC-managed JavaScript reference |
| `HostObject | None` | `object | null` |
| Snek class `C` / `C | None` | Opaque `SnekObject<"module.C">` / handle or `null` |
| `list[T]` with supported elements | Live, GC-managed `SnekList<T>` handle |
| `Callable[[T, ...], R]` | Typed synchronous function, or async function when `R` is a `Coroutine` |
| `None` return | `void` / `undefined` |

The generated `.d.ts` describes an instance's exported methods. Wrappers also
validate types, argument counts and integer ranges at runtime. Every parameter
must be supplied, including parameters with Snek defaults; calls are positional.
Float infinities, NaN and signed zero follow Wasm arithmetic. JavaScript strings
must contain valid Unicode scalar values: lone UTF-16 surrogates are rejected.

Strings are copied into the GC heap before user code runs and copied back for
JavaScript results. Supported Snek lists cross as live handles and Snek class
instances cross as opaque handles; other collections stay internal. `HostObject` passes a JavaScript-owned
external reference without copying it, and `Callable` exposes a JavaScript
function backed by a GC-managed closure.
Temporary linear memory is reused after each call, so callers never free values;
its capacity can remain at the largest call's high-water mark.
Exported string parameters and host string returns also enable full-Unicode tables for the operations used by the
program, so case conversion and character classification work on host input even
when those characters never appear in the source.

## Snek-owned list handles

Export a function returning `list[T]` to create a live `SnekList<T>` handle in
JavaScript. Supported element types are `i32`, `i64`, `f32`, `f64`, `bool`, `str`,
`HostObject`, `HostObject | None`, Snek classes and nullable class references,
and lists of those types:

```python
def words() -> list[str]:
    return ["hello", "world"]

def join_words(items: list[str]) -> str:
    return " ".join(items)
```

```javascript
const words = lib.words();
words.set(0, "hello 🐍");
words.append("from Snek");
console.log(words.length, words.get(-1));
console.log(lib.join_words(words));
const snapshot = words.toArray(); // a separate JavaScript array
const last = words.pop();
```

Handles expose read-only `length` and typed `get`, `set`, `append`, `pop` and
`toArray` methods. Indices must be safe integers; negative indices count from the
end. Invalid indices and popping an empty list throw JavaScript `RangeError`.
Element validation follows the scalar/string/object rules, including the
[class handle](#snek-owned-class-handles) subtype and ownership checks. `toArray()`
is a shallow copy: object elements retain their JavaScript identity, and nested
list elements remain live `SnekList` handles.

Passing a handle into Snek does not copy the list. Mutations on either side are
visible to the other, and returning the same list from the same instance returns
the same JavaScript wrapper. WasmGC manages its lifetime: keep the handle while
you need it, then let JavaScript collect it. There is no `free()` or `dispose()`.

Lists work in sync and async exports and `@host` parameters/results. Host callbacks
may inspect and mutate received handles immediately, without re-entering Snek
user code. Handles also remain usable between suspended async steps. This does
not permit calling ordinary exports or Snek callbacks during a synchronous step.
Concurrent invocations sharing a list see each other's changes; handles do not
provide transaction isolation.

Create lists in Snek, then pass their handles back. JavaScript arrays, lookalike
objects, handles for different element types, and handles from another instance
are rejected. Handles are not transferable between workers; send `toArray()` data
instead. Dictionary/tuple handles, callback list elements, and
optional lists are not supported yet. Supported lists can also be parameters and
results of [typed callbacks](#typed-callbacks).

### Nested lists

Types such as `list[list[str]]` and `list[list[list[i32]]]` use nested
`SnekList` types in TypeScript. `get()` and `pop()` return child handles;
`set()` and `append()` accept child handles of exactly the expected type from
the same instance. Shared child lists retain their identity:

```python
def rows() -> list[list[str]]:
    shared = ["one", "two"]
    return [shared, shared]
```

```javascript
const rows = lib.rows();
console.log(rows.get(0) === rows.get(1)); // true
rows.get(0).append("three");
console.log(rows.get(1).get(-1)); // "three"
const snapshot = rows.toArray().map(row => row.toArray());
```

Removing a child from its parent does not invalidate an existing child handle.
Nested handles also work in host callbacks, async returns, and `gather()` results.
There is no automatic recursive array conversion: choose explicitly which levels
to copy, as above. Nullability is supported for `HostObject` and Snek class
elements, not for child lists themselves.

## Snek-owned class handles

Snek-created class instances can cross export and `@host` boundaries,
including async parameters/results and nullable references:

```python
from snek import i32

class Counter:
    value: i32

    def __init__(self, value: i32) -> None:
        self.value = value

def make_counter(value: i32) -> Counter:
    return Counter(value)

def increment(counter: Counter) -> i32:
    counter.value += 1
    return counter.value
```

```javascript
const counter = lib.make_counter(10);
console.log(lib.increment(counter)); // 11
```

Export both functions when building. JavaScript receives a frozen opaque handle,
not a copy of the object's fields. The handle keeps the GC object alive without
`free()` or `dispose()`. Fields, methods and constructors are not exposed
automatically: use explicitly exported functions to create or manipulate objects.

Returning the same object preserves JavaScript identity, including through a
base-class annotation. Derived objects satisfy base-class parameters, and Snek
method dispatch still uses the actual class. Unrelated classes, lookalike JS
objects and handles from another instance are rejected. `C | None` accepts `null`,
not `undefined`. Async host results use the same validation rules; invalid resolved
handles become catchable Snek `RuntimeError`s.

Generated `SnekObject` types are nominal and preserve the inheritance hierarchy.
Prefer inference, or `ReturnType<typeof lib.make_counter>`, when naming a handle
type in application code. The explicit type uses its module-qualified class name,
such as `SnekObject<"counters.Counter">` for a class in `counters.py`. TypeScript
permits derived-to-base assignment, but not implicit downcasts or plain objects.
Runtime instance ownership is checked even when TypeScript types match.

Handles are not transferable to another worker. They can be stored by JavaScript
host callbacks, but calling an export during an active synchronous step is still
forbidden.

### Classes in lists and callbacks

Class handles also work inside lists and synchronous/async `Callable` signatures,
including nullable classes and nested lists. A `list[Counter]` becomes a
`SnekList<SnekObject<"module.Counter">>`; its elements are the same handles that
ordinary exports return. Removing an element does not invalidate its handle.
Lists remain invariant: `list[Derived]` is not a `list[Base]`, although appending
a derived object to a `list[Base]` is valid. Both TypeScript and runtime checks
enforce these rules.

```python
from typing import Callable

def transform(fn: Callable[[Counter], Counter], value: Counter) -> Counter:
    return fn(value)
```

```javascript
const same = lib.transform(value => value, counter);
console.log(same === counter); // true
```

An async callback returning a class uses `Coroutine[None, None, Counter]` and
resolves to a handle in JavaScript. Wrong-class and foreign-instance results are
rejected. Types used only inside lists or callback signatures still generate the
required wrappers and nominal TypeScript declarations. Class fields stay private
in every context; list operations do not expose them or relax re-entry rules.

## Opaque JavaScript objects

Import `HostObject` from `snek` to receive, retain and pass JavaScript objects.
JavaScript still owns their behavior. Snek cannot read properties, call methods,
construct them, convert them to strings, order them or use them as dictionary
keys. Instead, declare typed host functions for the operations you need:

```python
from snek import HostObject, host

@host("query")
def query(selector: str) -> HostObject | None:
    pass

@host("set_text")
def set_text(element: HostObject, text: str) -> None:
    pass

def update(selector: str, text: str) -> bool:
    element = query(selector)
    if element is None:
        return False
    set_text(element, text)
    return True
```

```javascript
const library = await instantiate(wasmUrl, { imports: {
  query: selector => document.querySelector(selector),
  set_text: (element, text) => {
    if (!(element instanceof Element)) throw new TypeError("Expected a DOM element");
    element.textContent = text;
  },
}});
library.update("#result", "Hello from Snek 🐍!");
```

The host checks any more specific shape it needs. The generated TypeScript type
is `object`, not `Element` or `any`; narrow it in the callback. `HostObject | None`
maps only `null` to `None`; `undefined` is rejected. Primitives are rejected too.
JavaScript functions can be held as opaque objects, but cannot be called by Snek.
Promises and thenables are not supported as opaque objects; use an async host
function to resolve an object instead. Objects must remain non-thenable while
crossing the boundary, including when returned from async exports.

These values use Wasm external references. The engine keeps a JavaScript object
alive while reachable from a Snek local, field, collection, closure, generator or
coroutine. There are no integer IDs, reference tables or manual retain/release
calls. Ordinary GC lifetime rules apply; explicit resources (event listeners,
sockets, and so on) still need application cleanup. Identity is preserved:
`is`, `is not`, `==` and `!=` compare the underlying JavaScript object by identity,
including collection membership/equality. Nullable values support `is None`
narrowing. Representations such as `str(value)` and `list.index(value)` remain
unsupported because an opaque object has no Snek representation.

`HostObject` requires a library build. It is separate from Snek-owned class/list
handles and does not provide a DOM API, make an object transferable to another worker,
or permit synchronous re-entry. The Vite example passes a DOM
element through its `identity` export and verifies that it is the same object.

## Typed callbacks

Use `Callable` to pass JavaScript functions to Snek or return Snek functions and
closures to JavaScript:

```python
from typing import Callable

def apply(fn: Callable[[str], str], text: str) -> str:
    return fn(text)

def greeter(prefix: str) -> Callable[[str], str]:
    def greet(name: str) -> str:
        return prefix + name
    return greet
```

Export `apply` and `greeter`, then call them through the generated bindings:

```javascript
lib.apply(text => text.toUpperCase(), "hello"); // HELLO
const greet = lib.greeter("Hello, ");
greet("browser"); // Hello, browser
```

Callback arguments and results can be numbers, booleans, strings, `HostObject`,
or `HostObject | None`, Snek class/nullable class handles, plus supported `list[T]`
handles (including nested lists);
results can also be `None`. Nested callbacks, collections other than supported
lists, and optional callbacks are not supported. Callbacks with these result types
are synchronous: returning a Promise is an error, including for `None` results.
The generated TypeScript uses `undefined` for a `None` callback result to catch
accidentally async functions.

Host declarations can accept and return callbacks too. An async host declaration
or async export may resolve to a synchronous callback. For DOM events, a host
function can retain a Snek callback and register a JavaScript listener that calls
it later. Adapt the event to the declared parameter types; a callback does not
automatically receive browser globals or a `this` receiver.

Closures retain their captures, including shared `nonlocal` state, for as long as
they are reachable. There is no manual release operation. Remove event listeners
and other application references when no longer needed. Round trips through the
same instance and signature preserve function identity. A callback from another
instance is treated as a JavaScript function owned by that instance.

Calling a Snek callback enters its instance just like an export. It fails if that
instance is already executing a synchronous step, so host functions must
not invoke a received Snek callback inline. Deferred events can invoke it after
the current step finishes, including while an async call is suspended. Exceptions and boundary validation follow the same
rules as exports and synchronous host imports.

### Async callbacks

Declare a callback returning `Coroutine[None, None, T]` when it performs async
work. JavaScript supplies a function returning `PromiseLike<T>`; Snek awaits it:

```python
from typing import Callable, Coroutine

async def transform(fetch_text: Callable[[str], Coroutine[None, None, str]], url: str) -> str:
    return (await fetch_text(url)).upper()
```

```javascript
const text = await lib.transform(async url => {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.text();
}, "./message.txt");
```

Snek can also return async functions and closures using the same annotation.
Their JavaScript wrappers return Promises, and calls can overlap with other async
invocations. Passing a returned closure back into the same instance preserves its
identity. Host declarations may accept or return async callbacks as well.

Async callback results use the same scalar/string/object-handle/list types as sync
callbacks, plus `None`. `Coroutine[None, None, None]` maps to `PromiseLike<void>`.
Returning a bare value is rejected. Promise rejections, synchronous throws, and
invalid resolved values become catchable Snek `RuntimeError`s; awaited `finally`
cleanup runs. Uncaught Snek exceptions from returned async closures reject their
JavaScript Promise with `SnekError`.

Creating a Snek coroutine does not start its callback until it is awaited. A
JavaScript call to a returned async closure starts immediately, just like an async
export. Synchronous re-entry remains forbidden; an async callback must not call a
Snek wrapper on the same instance before first yielding back to JavaScript.

### Callbacks with list handles

List parameters and results follow the same identity, mutation and instance
ownership rules as ordinary exports. A JavaScript callback may read or mutate a
received handle and return it to Snek:

```python
from typing import Callable

def transform(fn: Callable[[list[str]], list[str]], items: list[str]) -> list[str]:
    return fn(items)
```

```javascript
const transformed = lib.transform(items => {
  items.append("from JavaScript");
  return items;
}, words);
console.log(transformed === words); // true
```

Snek closures exposed to JavaScript can likewise accept or return list handles.
Async signatures use `Coroutine[None, None, list[T]]`; JavaScript callbacks must
return a Promise resolving to the correct handle, not an array. Nested lists and
multiple list parameters are supported. Types used only inside callback signatures
still get their list wrappers and TypeScript declarations. Calling a Snek closure
during a synchronous Snek step remains forbidden; primitive list-handle operations
inside a JavaScript callback are allowed.

## Initialization, errors and output

Each `instantiate` creates an independent instance and runs module initialization
once. The entry's `__name__` is its module name, not `"__main__"`, so command-only
main guards do not execute. Local imports work with the usual `--module-root`
rules. Select functions defined in the entry module; wrap imported functions in
an entry function when you want to expose them.

An uncaught Snek exception becomes a JavaScript `SnekError` with its class name and
message. Failed initialization rejects `instantiate`. Wasm traps and host callback
errors propagate as JavaScript errors. Reentrant calls into the same instance
are rejected.

`print()` is supported, but output is discarded unless you supply a callback:

```javascript
const lib = await instantiate(bytes, { stdout: text => process.stdout.write(text) });
```

Callbacks receive text chunks, not necessarily whole lines. Snek exception output
is captured in the thrown error instead of printed to stderr. Only selected
functions appear on the returned, frozen API object.

## Current limits

Exports must be entry-module functions with the boundary types above (or coroutines
returning those types). Collections other than the supported list handles,
optional types other than `HostObject`/class references, generators and raw coroutine objects cannot cross
the boundary. Libraries can use these internally where supported by the language,
but cannot perform async module initialization or use WASI services such as
stdin, environment variables, clocks and randomness. Unsupported signatures and
services fail at compile time, including services referenced by unexported code.

`--library` requires one or more `--export` flags and cannot be combined with
`--component`. This is a core-Wasm JavaScript library target, not a WIT component
library. Native command and WASI component targets are unchanged.

Export names are ASCII identifiers. `memory`, `then`, and the `__snek_` prefix
are reserved for the bridge and loader.
