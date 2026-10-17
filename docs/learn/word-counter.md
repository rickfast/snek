# A small word counter

This example combines Unicode text processing, a typed dictionary, and a
deterministically sorted report. Save it as `word-count.snek`, or run the copy
in `docs/examples/`.

<!-- example: word-count.snek -->

```sh
python -m snekc run docs/examples/word-count.snek --no-opt
```

```text
hello: 2
snek: 1
strasse: 2
```

## Why these annotations?

`dict[str, i32]` fixes both key and value types. An empty dictionary needs an
annotation because there are no elements from which to infer those types.
`counts.get(word, 0)` has an explicit integer default: machine integers cannot
be `None`, so an optional integer result is not a valid Snek type.

## Unicode, not just ASCII

`casefold()` normalizes case for caseless matching; `Straße` and `STRASSE` both
become `strasse`. `split()` without an argument splits on Unicode whitespace.
This is intentionally a small counter: punctuation is retained, and there is
no natural-language tokenization or Unicode normalization step.

## Views and sorting

`counts.keys()` is a live view. `list()` makes a snapshot that `sorted()` can
order without changing the dictionary. Dictionary iteration itself preserves
insertion order; sorting here is an explicit presentation choice.

For larger programs, use the [collection reference](../language/collections.md)
and [string reference](../language/strings.md). To read a byte stream from stdin,
see the [standard library](../reference/stdlib.md); text stdin is not implemented yet.
