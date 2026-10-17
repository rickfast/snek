"""Filesystem boundary for static projects. Language linking lives in Snek."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from .errors import CompileError


@dataclass
class Source:
    path: Path
    text: str | None
    package: bool = False


class Project:
    def __init__(self, path: Path, source: str, root: Path | None = None):
        self.root = (root or path.resolve().parent).resolve()
        path = path.resolve()
        try:
            relative = path.relative_to(self.root)
        except ValueError:
            raise ValueError("entry source must be inside --module-root") from None
        parts = list(relative.with_suffix("").parts)
        package = parts[-1] == "__init__"
        if package:
            parts.pop()
            if not parts:
                raise ValueError("a package initializer entry requires --module-root above its package directory")
        if parts and not parts[-1].isidentifier():
            parts[-1] = "__entry__"
        if not parts or not all(part.isidentifier() for part in parts):
            raise ValueError("project source paths must use Python identifiers")
        self.entry = ".".join(parts)
        self.sources = {self.entry: Source(path, source.replace("\r\n", "\n").replace("\r", "\n"), package)}

    def add(self, name: str) -> None:
        if not name or not all(part.isidentifier() for part in name.split(".")):
            raise ValueError(f"invalid module request: {name!r}")
        base = self.root.joinpath(*name.split("."))
        candidates = [Path(str(base) + ".snek"), Path(str(base) + ".py"),
                      base / "__init__.snek", base / "__init__.py"]
        found = [p for p in candidates if p.is_file()]
        if len(found) > 1:
            raise ValueError(f"ambiguous module '{name}': " + ", ".join(map(str, found)))
        if not found:
            self.sources[name] = Source(base, None)
            return
        path = found[0].resolve()
        if not path.is_relative_to(self.root):
            raise ValueError(f"module '{name}' resolves outside --module-root")
        if any(s.path == path and key != name for key, s in self.sources.items()):
            raise ValueError(f"module '{name}' aliases another source file")
        self.sources[name] = Source(path, path.read_text(encoding="utf-8"), path.stem == "__init__")

    def envelope(self) -> str:
        lines = ["SNEK-PROJECT-1", self.entry, str(len(self.sources))]
        for name, source in self.sources.items():
            path = str(source.path)
            if "\n" in path or "\r" in path:
                raise ValueError("project paths cannot contain newlines")
            lines.extend([name, path, str(int(source.package)), str(-1 if source.text is None else len(source.text))])
            if source.text is not None:
                lines.append(source.text)
        return "\n".join(lines) + "\n"

    def locate(self, error: CompileError) -> CompileError:
        offset = 0
        for source in self.sources.values():
            length = (source.text or "").count("\n") + 2
            if error.lineno is not None and offset < error.lineno <= offset + length:
                error.lineno -= offset
                error.filename = str(source.path)
                error.source = source.text
                break
            offset += length
        error.msg = re.sub(r"@user:([\w~]+):([\w]+)", lambda m: m[1].replace("~", ".") + "." + m[2], error.msg)
        error.args = (error.msg,)
        return error

    def owned_tree(self):
        from bootstrap.modules import module_project
        while True:
            try:
                return module_project(self.envelope())
            except ValueError as error:
                message = str(error)
                if message.startswith("SNEK-MODULE-NEEDED "):
                    name = message[len("SNEK-MODULE-NEEDED "):]
                    if name in self.sources:
                        raise RuntimeError("module resolver made no progress") from error
                    self.add(name)
                    continue
                match = re.fullmatch(r"line (\d+), column (\d+): (.*)", message, re.S)
                if match:
                    problem = CompileError(match[3])
                    problem.lineno, problem.col = int(match[1]), int(match[2]) - 1
                    raise self.locate(problem) from None
                raise

    def compile(self, *, wat: bool = False, component: bool = False) -> bytes:
        from .selfhost import invoke
        while True:
            try:
                output = invoke(self.envelope().encode(), ["--project", *(["--wat"] if wat else []), *(["--component"] if component else [])])
            except CompileError as error:
                raise self.locate(error) from None
            if not output.startswith(b"SNEK-MODULE-NEEDED "):
                return output
            request = output[len(b"SNEK-MODULE-NEEDED "):].decode().strip()
            if request in self.sources:
                raise RuntimeError("module resolver made no progress")
            self.add(request)
