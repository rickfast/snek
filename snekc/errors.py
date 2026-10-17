from __future__ import annotations

import ast


class CompileError(Exception):
    """A user-facing compile error tied to a source location."""

    def __init__(self, msg: str, node: ast.AST | None = None):
        super().__init__(msg)
        self.msg = msg
        self.lineno: int | None = getattr(node, "lineno", None)
        self.col: int | None = getattr(node, "col_offset", None)

    def format(self, filename: str, source: str | None = None) -> str:
        filename = getattr(self, "filename", filename)
        source = getattr(self, "source", source)
        loc = filename
        if self.lineno is not None:
            loc += f":{self.lineno}:{(self.col or 0) + 1}"
        out = f"{loc}: error: {self.msg}"
        if source and self.lineno:
            lines = source.splitlines()
            if 0 < self.lineno <= len(lines):
                out += f"\n    {lines[self.lineno - 1]}\n    {' ' * (self.col or 0)}^"
        return out
