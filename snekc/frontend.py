"""Selectable source parsers and the temporary owned-AST -> checker adapter.

The snek frontend executes the Snek implementation with the Python shim while
the checker/emitter are still Python. It never calls ast.parse, even for built-in
source or quoted annotations. The same adapter accepts the compiled parser's
JSON for end-to-end parity tests; ast classes are only an intermediate API.
"""
from __future__ import annotations

import ast
import os
import re
from typing import Any

from .errors import CompileError

FRONTENDS = ("cpython", "snek")
_TYPES = {
    name: getattr(ast, name) for name in (
        "Module Expression FunctionDef AsyncFunctionDef ClassDef Return Delete Assign AnnAssign "
        "AugAssign For AsyncFor While If With AsyncWith Raise Try Assert Import ImportFrom "
        "Global Nonlocal Expr Pass Break Continue BoolOp BinOp UnaryOp Lambda IfExp Dict List "
        "Tuple ListComp DictComp GeneratorExp comprehension Await Yield YieldFrom Compare Call FormattedValue JoinedStr Constant Attribute "
        "Subscript Starred Name Slice arguments arg keyword alias withitem ExceptHandler "
        "And Or Add Sub Mult MatMult Div Mod Pow LShift RShift BitOr BitXor BitAnd FloorDiv "
        "Invert Not UAdd USub Eq NotEq Lt LtE Gt GtE Is IsNot In NotIn"
    ).split()
}


def select(frontend: str | None = None) -> str:
    name = frontend if frontend is not None else os.environ.get("SNEK_FRONTEND", "cpython")
    if name not in FRONTENDS:
        raise CompileError(f"unknown frontend {name!r}; choose cpython or snek")
    return name


def _convert(node: Any) -> Any:
    # Both the in-process Node and its JSON encoding pass through the same
    # conversion. No reparsing of literals or generated Python source is used.
    from bootstrap.parser import Node

    if isinstance(node, Node):
        if node.kind == "str":
            return node.text
        if node.kind == "none":
            return None
        if node.kind == "scalar":
            if node.text in ("true", "false"):
                return node.text == "true"
            return int(node.text)
        if node.kind == "list":
            return [_convert(item) for item in node.items]
        kind = node.kind
        fields = dict(zip(node.keys, node.items, strict=True))
        line, column = node.line, node.column
    elif isinstance(node, list):
        return [_convert(item) for item in node]
    elif isinstance(node, dict):
        kind = node.get("_type")
        fields = {k: v for k, v in node.items() if k not in ("_type", "_line", "_column")}
        line, column = node.get("_line", 0), node.get("_column", 0)
    else:
        return node
    if kind == "Number":
        text = _convert(fields["text"]).replace("_", "")
        try:
            if text.lower().startswith(("0x", "0o", "0b")):
                return int(text, 0)
            return float(text) if "." in text or "e" in text.lower() else int(text, 10)
        except ValueError as error:
            raise CompileError(f"invalid numeric literal: {error}") from None
    if kind == "Ellipsis":
        return Ellipsis
    cls = _TYPES.get(kind)
    if cls is None:
        raise ValueError(f"unknown owned AST node: {kind!r}")
    if fields.keys() - set(cls._fields):
        raise ValueError(f"unexpected fields for owned AST {kind}")
    values = {key: _convert(value) for key, value in fields.items()}
    for key in cls._fields:
        if key in values:
            continue
        if key == "ctx":
            values[key] = ast.Load()
        elif key in ("type_params", "type_ignores"):
            values[key] = []
        elif key in ("kind", "type_comment"):
            values[key] = None
        else:
            raise ValueError(f"missing owned AST {kind}.{key}")
    result = cls(**values)
    if line and "lineno" in cls._attributes:
        result.lineno, result.col_offset = line, column
    return result


def _target(node: ast.AST, context: type[ast.expr_context]) -> None:
    # Receivers and indices stay Load: a[i].field = v stores only the field.
    if isinstance(node, (ast.Name, ast.Attribute, ast.Subscript, ast.Starred, ast.Tuple, ast.List)):
        node.ctx = context()
    if isinstance(node, (ast.Tuple, ast.List)):
        for item in node.elts:
            _target(item, context)
    elif isinstance(node, ast.Starred):
        _target(node.value, context)


def to_ast(owned: Any) -> ast.AST:
    """Adapt an owned Node or decoded parser JSON; restore Store/Del contexts."""
    tree = _convert(owned)
    if not isinstance(tree, (ast.Module, ast.Expression)):
        raise ValueError("owned AST root must be Module or Expression")
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for value in node.targets:
                _target(value, ast.Store)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign, ast.For, ast.AsyncFor, ast.comprehension)):
            _target(node.target, ast.Store)
        elif isinstance(node, ast.Delete):
            for value in node.targets:
                _target(value, ast.Del)
        elif isinstance(node, ast.withitem) and node.optional_vars is not None:
            _target(node.optional_vars, ast.Store)
    _locations(tree, 1, 0)
    return tree


def _locations(node: ast.AST, line: int, column: int) -> None:
    # The owned AST has diagnostic origins, not exact end spans. Inherit an
    # origin for synthesized nodes and use zero-width ends, not bogus line 1
    # ranges from ast.fix_missing_locations on a tree without end positions.
    if "lineno" in node._attributes:
        line = getattr(node, "lineno", line)
        column = getattr(node, "col_offset", column)
        node.lineno, node.col_offset = line, column
        node.end_lineno, node.end_col_offset = line, column
    for child in ast.iter_child_nodes(node):
        _locations(child, line, column)


def parse_source(source: str, filename: str = "<input>", mode: str = "exec", *,
                 frontend: str | None = None) -> ast.Module | ast.Expression:
    name = select(frontend)
    if mode not in ("exec", "eval"):
        raise ValueError(f"unsupported source parse mode: {mode}")
    if name == "cpython":
        try:
            return ast.parse(source, filename=filename, mode=mode)
        except SyntaxError as error:
            problem = CompileError(f"syntax error: {error.msg}")
            problem.lineno, problem.col = error.lineno, (error.offset or 1) - 1
            raise problem from None
    from bootstrap.parser import parse, parse_expression

    try:
        owned = parse(source) if mode == "exec" else parse_expression(source)
    except ValueError as error:
        match = re.fullmatch(r"line (\d+), column (\d+): (.*)", str(error), re.DOTALL)
        if not match:
            raise  # Internal parser failures must not masquerade as user syntax errors.
        problem = CompileError("syntax error: " + match[3])
        problem.lineno, problem.col = int(match[1]), int(match[2])
        raise problem from None
    return to_ast(owned)
