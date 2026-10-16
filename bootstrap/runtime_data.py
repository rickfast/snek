"""Fixed runtime data and source indexing during bootstrap preparation.

The owned Snek parser validates/indexes the library once, without CPython's AST
or checker. Only source fragments and locations are embedded, not parsed ASTs.
Rebuilds after the seed consume the same bundle without running this linker.
"""
from __future__ import annotations

import hashlib
import json
from string import Template

from snekc.builtins import STR_METHODS_TEMPLATE
from snekc.check import INTRINSICS
from snekc.formatting import FORMAT_SOURCE
from snekc.unicode import ALL_FLAGS, CASE_KINDS, FLAG_NAMES, NONPRINT
from bootstrap.parser import Node, parse, sequence, string


def runtime_functions(source: str) -> list[tuple[str, int, str]]:
    # Match the lexer's newline normalization, not str.splitlines()'s broader
    # Unicode/form-feed separators (which can appear inside string literals).
    source = source.replace("\r\n", "\n").replace("\r", "\n")
    nodes = parse(source).get("body").items
    lines = source.split("\n")
    result = []
    names = set()
    for index, node in enumerate(nodes):
        if node.kind != "FunctionDef":
            raise ValueError("runtime source must contain only functions")
        name = node.get("name").text
        if name in names:
            raise ValueError("duplicate runtime helper: " + name)
        names.add(name)
        end = nodes[index + 1].line - 1 if index + 1 < len(nodes) else len(lines)
        text = "\n".join(lines[node.line - 1:end])
        if index + 1 < len(nodes):
            text += "\n"
        result.append((name, node.line, text))
    return result


def indexed_runtime(source: str, contract: str) -> Node:
    return Node("Module").put("runtime_abi", string(contract)).put("body", sequence([
        Node("FunctionSource", text, line).put("name", string(name))
        for name, line, text in runtime_functions(source)
    ]))


def configuration() -> tuple[str, dict[str, list[str]], str]:
    source = Template(STR_METHODS_TEMPLATE).substitute(FLAG_NAMES) + FORMAT_SOURCE
    intrinsics = {name: [function, str(ret), *(str(param) for param in params)]
                  for name, (params, ret, function) in INTRINSICS.items()}
    # Also binds Unicode-use metadata to the emitter's current flag/case ABI.
    payload = json.dumps([source, intrinsics, ALL_FLAGS, CASE_KINDS, NONPRINT], sort_keys=True)
    return source, intrinsics, hashlib.sha256(payload.encode()).hexdigest()
