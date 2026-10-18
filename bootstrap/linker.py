"""Token-aware preparation of the fixed, single-module compiler bundle.

This is a host build tool, never part of stages 1/2/3. The explicit manifest
lists the modules whose imports may be flattened. Unknown imports, executable
top-level statements, duplicate definitions, and missing selections fail closed.
No CPython AST is used to compile or rewrite Snek programs.
"""
from __future__ import annotations

from dataclasses import dataclass
import io
from pathlib import Path
import tokenize

ROOT = Path(__file__).resolve().parent
MODULE_PATHS = {"runtime_abi": ROOT.parent / "snekc/runtime_abi.py"}
MANIFEST = {
    "runtime_abi": ("__future__",),
    "unicode_names": ("__future__", "snek"),
    "parser": ("__future__", "snek", "bootstrap.unicode_names"),
    "encode": ("__future__", "snek"),
    "declarations": ("__future__", "snek", "bootstrap.parser", "bootstrap.encode"),
    "formats": ("__future__", "snek", "bootstrap.parser", "bootstrap.declarations"),
    "scopes": ("__future__", "bootstrap.parser", "bootstrap.declarations"),
    "modules": ("__future__", "snek", "bootstrap.parser", "bootstrap.declarations", "bootstrap.scopes"),
    "library": ("__future__", "snek", "bootstrap.parser", "bootstrap.emit_data", "bootstrap.machine_emit"),
    "bodies": ("__future__", "snek", "bootstrap.parser", "bootstrap.formats", "bootstrap.scopes", "bootstrap.declarations"),
    "emit_support": ("__future__", "snek", "bootstrap.declarations"),
    "emit_program": ("__future__", "snek", "bootstrap.parser", "bootstrap.declarations", "bootstrap.bodies", "bootstrap.emit_support", "snekc.runtime_abi"),
    "emit_runtime": ("__future__", "snek", "bootstrap.declarations", "bootstrap.bodies", "bootstrap.emit_program"),
    "emit_host": ("__future__", "snek", "bootstrap.declarations", "bootstrap.emit_program", "bootstrap.emit_data", "snekc.runtime_abi"),
    "machine_lower": ("__future__", "snek", "bootstrap.parser"),
    "machine_emit": ("__future__", "snek", "bootstrap.parser"),
    "tasks": ("__future__", "bootstrap.parser", "bootstrap.machine_emit"),
    "emit_suspension": ("__future__", "snek", "bootstrap.parser", "bootstrap.declarations", "bootstrap.bodies", "bootstrap.emit_program", "bootstrap.machine_lower", "bootstrap.machine_emit"),
    "emit_module": ("__future__", "snek", "bootstrap.declarations", "bootstrap.bodies", "bootstrap.parser", "bootstrap.emit_data", "bootstrap.emit_program", "bootstrap.emit_support", "bootstrap.emit_runtime", "bootstrap.emit_host", "bootstrap.emit_suspension", "bootstrap.library", "bootstrap.tasks"),
}
HEADER = "from __future__ import annotations\nfrom snek import i32, i64, read_bytes, write_bytes, sys\n"
PARSER_PRIMITIVES = ("fail", "utf8", "json_string", "Node", "string", "scalar", "missing", "sequence")
NUMERIC_HELPERS = ("need", "digit", "big_trim", "big_mul", "big_shift", "big_cmp", "big_sub", "big_bits", "big_parse", "rounded_ratio", "float_bytes")


@dataclass(frozen=True)
class Statement:
    tokens: tuple[tokenize.TokenInfo, ...]
    source: str

    @property
    def words(self) -> list[str]:
        return [t.string for t in self.tokens if t.type not in {
            tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT,
            tokenize.COMMENT, tokenize.ENDMARKER}]


def statements(source: str) -> list[Statement]:
    """Split top-level statements without mistaking text inside literals for code."""
    tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    starts = []
    indent = 0
    brackets = 0
    for index, token in enumerate(tokens):
        if token.type == tokenize.INDENT:
            indent += 1
        elif token.type == tokenize.DEDENT:
            indent -= 1
        elif token.type not in {tokenize.NL, tokenize.NEWLINE, tokenize.COMMENT, tokenize.ENDMARKER}:
            if indent == 0 and brackets == 0 and token.start[1] == 0:
                starts.append(index)
            if token.type == tokenize.OP:
                if token.string in ("(", "[", "{"):
                    brackets += 1
                elif token.string in (")", "]", "}"):
                    brackets -= 1
    lines = [line + "\n" for line in source.split("\n")]
    lines[-1] = lines[-1][:-1]
    result = []
    for offset, start in enumerate(starts):
        end = starts[offset + 1] if offset + 1 < len(starts) else len(tokens)
        first_line = tokens[start].start[0] - 1
        last_line = tokens[end].start[0] - 1 if end < len(tokens) else len(lines)
        result.append(Statement(tuple(tokens[start:end]), "".join(lines[first_line:last_line])))
    return result


def rename_symbols(source: str, names: dict[str, str]) -> str:
    tokens = []
    previous = ""
    indent = 0
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.INDENT:
            indent += 1
        elif token.type == tokenize.DEDENT:
            indent -= 1
        if (token.type == tokenize.NAME and token.string in names and previous != "."
                and not (previous in ("def", "class") and indent > 0)):
            token = token._replace(string=names[token.string])
        tokens.append(token)
        if token.type not in {tokenize.NL, tokenize.COMMENT}:
            previous = token.string
    return tokenize.untokenize(tokens)


def flatten(source: str, allowed_imports: tuple[str, ...], *, select: tuple[str, ...] | None = None,
            rename: dict[str, str] | None = None, keep_main: bool = False) -> str:
    definitions = {}
    main = ""
    for statement in statements(source):
        words = statement.words
        if statement.tokens[0].type == tokenize.STRING:
            if len(words) != 1:
                raise ValueError("unsupported top-level string expression")
            continue
        if words[0] == "from":
            if "import" not in words:
                raise ValueError("invalid bundle import")
            at = words.index("import")
            module = "".join(words[1:at])
            names = words[at + 1:]
            if names and names[0] == "(" and names[-1] == ")":
                names = names[1:-1]
            valid = bool(names)
            for index, name in enumerate(names):
                valid = valid and ((name.isidentifier() and name != "as") if index % 2 == 0 else name == ",")
            if module not in allowed_imports or not valid or (module == "__future__" and names != ["annotations"]):
                raise ValueError("unapproved bundle import: " + " ".join(words))
            continue
        if words[0] in ("def", "class"):
            name = words[1]
            if name in definitions:
                raise ValueError("duplicate bundle definition: " + name)
            definitions[name] = statement.source
            continue
        if words[:3] == ["if", "__name__", "=="] and words[3] in ('"__main__"', "'__main__'") and words[4] == ":":
            if main:
                raise ValueError("duplicate bundle main guard")
            main = statement.source
            continue
        raise ValueError("unsupported bundle top-level statement: " + " ".join(words[:6]))
    selected = tuple(definitions) if select is None else select
    if len(set(selected)) != len(selected) or any(name not in definitions for name in selected):
        raise ValueError("missing or duplicate bundle selection")
    result = "\n".join(definitions[name] for name in selected)
    if keep_main:
        if not main:
            raise ValueError("bundle main guard missing")
        result += "\n" + main
    return rename_symbols(result, rename) if rename else result


def module_source(name: str, **options) -> str:
    if name not in MANIFEST:
        raise ValueError("module absent from bundle manifest: " + name)
    return flatten(MODULE_PATHS.get(name, ROOT / (name + ".py")).read_text(), MANIFEST[name], **options)


def parser_source(*, entry: bool = True) -> str:
    return HEADER + module_source("unicode_names") + "\n" + module_source("parser", keep_main=entry)


if __name__ == "__main__":
    import argparse

    cli = argparse.ArgumentParser(description="Link the standalone parser and pinned Unicode data")
    cli.add_argument("--out", type=Path, required=True)
    options = cli.parse_args()
    options.out.parent.mkdir(parents=True, exist_ok=True)
    options.out.write_text(parser_source())
