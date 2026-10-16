"""Type checking and source-order argument binding for sorted/min/max."""
from __future__ import annotations

import ast

from . import ir
from .errors import CompileError
from .types import BOOL, I32, NONE, STR, CallableT, ListT, OptionalT, Prim, is_assignable


class OrderedBuiltinChecker:
    def check_ordered_builtin(self, name, node, expected):
        keywords = {}
        allowed = {"key", "reverse"} if name == "sorted" else {"key", "default"}
        for kw in node.keywords:
            if kw.arg not in allowed:
                raise CompileError(f"{name}() got an unexpected keyword argument '{kw.arg}'", kw)
            if kw.arg in keywords:
                raise CompileError(f"{name}() got multiple values for '{kw.arg}'", kw)
            keywords[kw.arg] = kw.value
        if len(node.args) not in ((1,) if name == "sorted" else (1, 2)):
            raise CompileError(f"{name}() needs a list" + (" or two values" if name != "sorted" else ""), node)
        if len(node.args) == 2 and "default" in keywords:
            raise CompileError(f"{name}() cannot use default with multiple positional arguments", node)

        key_node = keywords.get("key")
        has_key = key_node is not None and not (isinstance(key_node, ast.Constant) and key_node.value is None)
        # Checking a named key first supplies the element type for sorted([]).
        # This is type checking only: execution order is fixed by the prelude below.
        key = self.check_expr(key_node) if has_key and not isinstance(key_node, ast.Lambda) else None
        if key is not None and not isinstance(key.ty, CallableT):
            raise CompileError(f"{name}() key must be a non-optional Callable, got {key.ty}; narrow optional keys first", key_node)
        if key is not None and len(key.ty.params) != 1:
            raise CompileError(f"{name}() key must take exactly one argument", key_node)

        default_node = keywords.get("default")
        fallback = None
        source = (node.args[0] if len(node.args) == 1 else
                  ast.copy_location(ast.List(elts=node.args, ctx=ast.Load()), node))
        hint = expected if name == "sorted" and isinstance(expected, ListT) else None
        if isinstance(source, ast.List) and not source.elts and hint is None:
            if default_node is not None and key is None:
                fallback = self.check_expr(default_node, expected)
            elem = (key.ty.params[0] if key is not None else
                    fallback.ty if fallback is not None else expected if name != "sorted" else None)
            if elem == NONE:
                raise CompileError("an empty list with a None default needs a list element type", source)
            if elem is None and name != "sorted":
                elem = I32
            hint = ListT(elem) if elem is not None else None
        xs = self.check_expr(source, hint)
        elem = self.list_elem(xs, source)
        if default_node is not None and fallback is None:
            fallback = self.check_expr(default_node, elem)

        if has_key:
            if key is None:
                key = self.check_key_lambda(key_node, elem)
            if not is_assignable(elem, key.ty.params[0]):
                raise CompileError(f"{name}() key must accept {elem}, got {key.ty.params[0]}", key_node)
            comparable = key.ty.ret
        else:
            comparable = elem
        if comparable != STR and not isinstance(comparable, Prim):
            if has_key:
                raise CompileError(f"{name}() key must return a number, bool, or str, got {comparable}", key_node)
            raise CompileError(f"{name}() supports lists of numbers, bools, or strings; got {xs.ty}", source)

        ret = xs.ty if name == "sorted" else elem
        if fallback is not None:
            if elem.is_ref and (fallback.ty == NONE or fallback.ty == OptionalT(elem)):
                ret = elem if isinstance(elem, OptionalT) else OptionalT(elem)
            fallback = self.coerce(fallback, ret, default_node, f"{name}() default")

        pre = []

        def capture(value):
            tmp = self.ctx.temp(value.ty)
            pre.append(ir.SetLocal(tmp, value))
            return self.ctx.get(tmp)

        xs = capture(xs)
        reverse = ir.Const(BOOL, False)
        for kw in node.keywords:
            if kw.arg == "key" and key is not None:
                key = capture(key)
            elif kw.arg == "reverse":
                reverse = capture(self.check_cond(kw.value))
            elif kw.arg == "default":
                fallback = capture(fallback)
        op = name
        args = [reverse] if name == "sorted" else []
        if fallback is not None:
            op += "_optional" if ret != elem else "_default"
            args = [fallback]
        result = (ir.KeyedListOp(ret, op, xs, key, args) if key is not None
                  else ir.ListOp(ret, op, xs, args))
        return ir.Seq(ret, pre, result)
