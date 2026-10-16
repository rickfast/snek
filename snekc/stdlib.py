"""Static standard-library namespaces and typed host operations.

Namespace imports are resolved independently of user locals. Named streams,
configuration, clocks and randomness do not require a general module loader.
"""
from __future__ import annotations

import ast

from . import ir
from .errors import CompileError
from .types import BOOL, CoroutineT, F64, I32, ListT, NONE, OptionalT, STR, StreamT


MODULE_EXPORTS = {
    "sys": {"stdin", "stdout", "stderr", "argv"},
    "os": {"getenv"},
    "time": {"time", "monotonic", "sleep"},
    "secrets": {"token_bytes", "token_hex", "randbelow"},
    "io": {"TextInput", "TextOutput", "BinaryInput", "BinaryOutput"},
}


def import_bindings(node):
    """Return local name -> library path, or None for an unrelated import."""
    bindings = {}
    if isinstance(node, ast.ImportFrom):
        module = (node.module or "").removeprefix("snek.")
        if node.module == f"snek.{module}" and module in MODULE_EXPORTS and not node.level:
            for alias in node.names:
                if alias.name not in MODULE_EXPORTS[module]:
                    raise CompileError(f"cannot import '{alias.name}' from {node.module}", node)
                bindings[alias.asname or alias.name] = (module, alias.name)
            return bindings
        if node.module == "snek" and not node.level:
            for alias in node.names:
                if alias.name in MODULE_EXPORTS:
                    bindings[alias.asname or alias.name] = (alias.name,)
            return bindings or None
    elif isinstance(node, ast.Import):
        if all(alias.name.removeprefix("snek.") in MODULE_EXPORTS and alias.name.startswith("snek.") for alias in node.names):
            for alias in node.names:
                bindings[alias.asname or "snek"] = (alias.name.removeprefix("snek."),) if alias.asname else ()
            return bindings
    return None


class StdlibChecker:
    def stdlib_type(self, node):
        path = self.stdlib_path(node)
        if path and len(path) == 2 and path[0] == "io" and path[1] in MODULE_EXPORTS["io"]:
            return StreamT(path[1])
        return None

    def check_stdlib_value(self, node):
        path = self.stdlib_path(node)
        if path == ("sys", "argv"):
            return ir.Call(ListT(STR), "snek.lib.argv", [])
        if path in (("sys", "stdin"), ("sys", "stdout"), ("sys", "stderr")):
            ty = StreamT("TextInput" if path[1] == "stdin" else "TextOutput")
            return ir.Call(ty, "snek.sys.get_" + path[1], [])
        return None

    def check_stream_attribute(self, recv, node):
        if node.attr == "closed":
            return ir.Call(BOOL, "snek.sys.closed", [recv])
        if node.attr == "buffer" and recv.ty.kind.startswith("Text"):
            return ir.Call(StreamT(recv.ty.kind.replace("Text", "Binary")), "snek.sys.buffer", [recv])
        raise CompileError(f"{recv.ty} has no attribute '{node.attr}'", node)

    def check_stream_call(self, recv, node, attr):
        if node.keywords:
            raise CompileError(f"{recv.ty}.{attr}() takes positional arguments only", node)
        binary = recv.ty.kind.startswith("Binary")
        output = recv.ty.kind.endswith("Output")
        if attr in ("close", "flush", "drain") and (output or attr == "close"):
            self.arity(node, 0)
            ret = CoroutineT(NONE) if attr == "drain" else NONE
            return ir.Call(ret, f"snek.sys.stream_{attr}", [recv])
        if output and attr == "write":
            self.arity(node, 1)
            ty = ListT(I32) if binary else STR
            value = self.coerce(self.check_expr(node.args[0], ty), ty, node.args[0])
            pre, recv, value = self.left_first(recv, value)
            return self.seq(pre, ir.Call(I32, "snek.sys.stream_write_bytes" if binary else "snek.sys.stream_write", [recv, value]))
        if binary and not output and attr in ("read", "readline"):
            if len(node.args) > 1:
                raise CompileError(f"{recv.ty}.{attr}() takes zero or one positional arguments", node)
            size = self.coerce(self.check_expr(node.args[0], I32), I32, node.args[0]) if node.args else ir.Const(I32, -1)
            pre, recv, size = self.left_first(recv, size)
            return self.seq(pre, ir.Call(CoroutineT(ListT(I32)), "snek.sys.stream_read", [recv, size, ir.Const(BOOL, attr == "readline")]))
        raise CompileError(f"unsupported stream operation '{recv.ty}.{attr}'", node)

    def stdlib_path(self, node):
        parts = []
        while isinstance(node, ast.Attribute):
            parts.insert(0, node.attr)
            node = node.value
        if not isinstance(node, ast.Name) or node.id not in self.library_imports:
            return None
        ctx = self.ctx
        if node.id in ctx.types or (ctx.scope and node.id in ctx.scope.bound):
            return None
        return (*self.library_imports[node.id], *parts)

    def check_stdlib_call(self, node):
        path = self.stdlib_path(node.func)
        if path is None:
            return None
        if path and path[0] == "sys":
            # Streams and argv are real values; ordinary receiver checking
            # also handles aliases, function results, fields and local shadowing.
            return None
        name = '.'.join(path)
        if node.keywords:
            raise CompileError(f"{name}() takes positional arguments only", node)
        if path == ("os", "getenv"):
            if len(node.args) not in (1, 2):
                raise CompileError("os.getenv() takes one or two positional arguments", node)
            key = self.coerce(self.check_expr(node.args[0], STR), STR, node.args[0])
            optional = OptionalT(STR)
            default = self.check_expr(node.args[1]) if len(node.args) == 2 else ir.NoneLit(optional)
            if default.ty not in (STR, NONE, optional):
                raise CompileError("os.getenv() default must be str or None", node)
            ret = STR if default.ty == STR else optional
            # Preserve key evaluation before a default with side effects.
            pre, key, default = self.left_first(key, self.coerce(default, optional, node))
            return self.seq(pre, ir.Call(ret, "snek.lib.getenv-required" if ret == STR else "snek.lib.getenv", [key, default]))
        if path in (("time", "time"), ("time", "monotonic")):
            self.arity(node, 0)
            return ir.Call(F64, "snek.lib." + path[-1], [])
        if path == ("time", "sleep"):
            self.arity(node, 1)
            value = self.coerce(self.check_expr(node.args[0], F64), F64, node.args[0], arith=True)
            return ir.Call(CoroutineT(NONE), "snek.lib.sleep", [value])
        if len(path) == 2 and path[0] == "secrets" and path[1] in MODULE_EXPORTS["secrets"]:
            self.arity(node, 1)
            value = self.coerce(self.check_expr(node.args[0], I32), I32, node.args[0])
            ret = {"token_bytes": ListT(I32), "token_hex": STR, "randbelow": I32}[path[1]]
            return ir.Call(ret, "snek.lib." + path[1], [value])
        raise CompileError(f"unsupported standard-library operation '{name}'", node)
