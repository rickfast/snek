"""Seed-construction owned typed IR -> Python emitter bridge.

This decodes resolved IR, never checks user source or falls back to check.py.
The existing emitter, encoder, Unicode tables and built-in exception allocation
ABI remain Python-owned for the seed only. This bridge is deliberately retained:
bootstrap_compiler.py uses it before stage 1; later stages forbid host passes.
"""
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
import shutil
import struct
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from snekc import ir, types as t
from snekc.errors import CompileError
from snekc.unicode import UnicodeUse


class IRBridge:
    def __init__(self):
        self.classes = {}

    def ty(self, value):
        if value.get("_type") != "Type":
            raise ValueError("expected an owned Type record")
        kind, name, args = value["kind"], value["name"], value["args"]
        primitives = {"i32": t.I32, "i64": t.I64, "f32": t.F32, "f64": t.F64,
                      "bool": t.BOOL, "str": t.STR, "None": t.NONE, "__Buf": t.BUF, "TracebackType": t.TRACEBACK}
        if kind in primitives and not args and not name:
            return primitives[kind]
        if kind == "Stream" and not args and name in ("TextInput", "TextOutput", "BinaryInput", "BinaryOutput"):
            return t.StreamT(name)
        if kind == "optional" and len(args) == 1 and not name:
            inner = self.ty(args[0])
            if not inner.is_ref or isinstance(inner, t.OptionalT):
                raise ValueError("optional IR type requires a non-optional reference")
            return t.OptionalT(inner)
        if kind == "list" and len(args) == 1 and not name:
            return t.ListT(self.ty(args[0]))
        if kind == "tuple" and not name:
            return t.TupleT(tuple(self.ty(arg) for arg in args))
        if kind == "dict" and len(args) == 2 and not name:
            key = self.ty(args[0])
            if not t.is_hashable(key):
                raise ValueError("unhashable dictionary IR key")
            return t.DictT(key, self.ty(args[1]))
        if kind == "view" and len(args) == 1 and name in ("keys", "values", "items"):
            mapping = self.ty(args[0])
            if not isinstance(mapping, t.DictT):
                raise ValueError("dictionary view needs a dictionary type")
            return t.DictViewT(mapping, name)
        if kind == "class" and name in self.classes and not args:
            return t.ClassT(self.classes[name])
        if kind == "type" and len(args) == 1 and not name:
            cls = self.ty(args[0])
            if not isinstance(cls, t.ClassT):
                raise ValueError("type IR requires a class")
            return t.TypeT(cls.info)
        if kind in ("Iterator", "Coroutine") and len(args) == 1 and not name:
            return (t.IteratorT if kind == "Iterator" else t.CoroutineT)(self.ty(args[0]))
        if kind == "Callable" and len(args) >= 1 and not name:
            return t.CallableT(tuple(self.ty(arg) for arg in args[:-1]), self.ty(args[-1]))
        if kind == "Cell" and len(args) == 1 and not name:
            return t.CellT(self.ty(args[0]))
        if kind == "Env" and name in ("", "layout"):
            return t.EnvT(tuple(self.ty(arg) for arg in args) if name else None)
        raise ValueError(f"unsupported owned IR type: {kind}")

    def runtime(self, declarations):
        """Lower the current prelude's exception allocation ABI, not user bodies.

        The signatures/layouts are supplied by the Snek declaration pass. An
        explicit source contract prevents silently ignoring changed builtin
        constructor bodies while this small runtime lowering is still Python.
        """
        from bootstrap.parser import parse
        from snekc.builtins import BUILTINS_SOURCE

        builtin_tree = parse(BUILTINS_SOURCE)
        expected_base = parse('''class BaseException:
    __message: Final[str | None]
    __keyrepr: Final[str | None] = None
    def __init__(self, __msg: str | None = None) -> None:
        self.__message = __msg
''').get("body").items[0]

        def shape(node):
            return node.kind, node.text, node.keys, [shape(child) for child in node.items]

        expected_names = []
        for node in builtin_tree.get("body").items:
            name = node.get("name").text
            expected_names.append(name)
            if name == "BaseException":
                if shape(node) != shape(expected_base):
                    raise ValueError("bootstrap runtime bridge needs the updated BaseException constructor")
            elif (len(node.get("body").items) != 1 or node.get("body").items[0].kind != "Pass"):
                raise ValueError("bootstrap runtime bridge only supports inherited exception constructors")
        records = [c for c in declarations["classes"] if c["builtin"]]
        if [c["name"] for c in records] != expected_names:
            raise ValueError("bootstrap runtime bridge expects only the current built-in exception classes")
        optional = t.OptionalT(t.STR)
        functions = []
        for record in records:
            if not record["builtin"] or record["methods"]:
                raise ValueError("built-in runtime bridge expects exception classes without methods")
            name = record["name"]
            # ClassInfo still carries a Python AST declaration, but the emitter
            # only uses its resolved metadata. No parsing/checking through ast.
            node = ast.ClassDef(name=name, bases=[], keywords=[], body=[], decorator_list=[])
            cls = t.ClassInfo(name, node, builtin=True)
            self.classes[name] = cls
            if record["base"]:
                cls.base = self.classes[record["base"]]
                cls.base.subclasses.append(cls)
            fields = record["fields"]
            if [f["name"] for f in fields] != ["__message", "__keyrepr"]:
                raise ValueError("built-in exception field layout changed")
            for index, f in enumerate(fields, 1):
                if self.ty(f["type"]) != optional or not f["final"] or f["index"] != index or f["owner"] != "BaseException":
                    raise ValueError("built-in exception field ABI changed")
                cls.fields[f["name"]] = t.FieldInfo(f["name"], optional, True, None, self.classes["BaseException"], index)
            constructor = record["constructor"]
            params = constructor["params"]
            if (len(params) != 1 or params[0]["name"] != "__msg" or self.ty(params[0]["type"]) != optional
                    or self.ty(constructor["returns"]) != t.NONE):
                raise ValueError("built-in exception constructor ABI changed")
            ty = t.ClassT(cls)
            value = ir.StructNew(ty, cls, [ir.LocalGet(optional, "__msg", optional), ir.NoneLit(optional)])
            functions.append(ir.Func(name + ".__new__", [("__msg", optional)], ty, {}, [ir.Return(value)]))
        return functions

    def user_classes(self, declarations):
        records = [c for c in declarations["classes"] if not c["builtin"]]
        for record in records:
            name = record["name"]
            if name in self.classes:
                raise ValueError("duplicate class IR record")
            node = ast.ClassDef(name=name, bases=[], keywords=[], body=[], decorator_list=[])
            self.classes[name] = t.ClassInfo(name, node)
        for record in records:
            cls = self.classes[record["name"]]
            if record["base"]:
                cls.base = self.classes[record["base"]]
                cls.base.subclasses.append(cls)
            for index, field in enumerate(record["fields"], 1):
                if field["index"] != index:
                    raise ValueError("non-contiguous class field layout")
                cls.fields[field["name"]] = t.FieldInfo(field["name"], self.ty(field["type"]), field["final"],
                                                      None, self.classes[field["owner"]], index)
            for method in record["methods"]:
                signature = method["signature"]
                sig = t.FuncSig([(p["name"], self.ty(p["type"])) for p in signature["params"]],
                                self.ty(signature["returns"]))
                # All binding/default checking has already happened in Snek.
                node = ast.FunctionDef(name=method["name"], args=ast.arguments(posonlyargs=[], args=[],
                                       kwonlyargs=[], kw_defaults=[], defaults=[]), body=[], decorator_list=[])
                cls.methods[method["name"]] = t.MethodInfo(method["name"], sig, self.classes[method["decl"]],
                                                         method["slot"], node, self.classes[method["owner"]])

    def expression(self, node):
        kind = node["_type"]
        ty = self.ty(node["type"])
        text = node["text"]
        args = [self.expression(arg) for arg in node["args"]]
        pre = [self.statement(stmt) for stmt in node["pre"]]
        if kind != "Seq" and pre:
            raise ValueError("only Seq expressions can contain a statement prelude")
        counts = {"Const": 0, "StrLit": 0, "NoneLit": 0, "LocalGet": 0, "Unary": 1, "Binary": 2,
                  "StrConcat": 2, "Compare": 2, "Logical": 2, "Convert": 1, "IsNull": 1,
                  "IfExpr": 3, "StrLen": 1, "ToStr": 1, "Seq": 1, "GetField": 1,
                  "ListGet": 2, "ListLen": 1, "ListPop": 1, "ListAppend": 2, "TupleGet": 1,
                  "ObjectEq": 2, "IsInstance": 1}
        if kind in counts and len(args) != counts[kind]:
            raise ValueError(f"wrong operand count for {kind}")
        if kind == "Const":
            if ty == t.BOOL:
                if text not in ("true", "false"):
                    raise ValueError("invalid boolean IR constant")
                return ir.Const(ty, text == "true")
            if ty in (t.I32, t.I64):
                value = int(text)
                bits = 32 if ty == t.I32 else 64
                if not -(2 ** (bits - 1)) <= value < 2 ** (bits - 1):
                    raise ValueError("integer IR constant out of range")
                return ir.Const(ty, value)
            if ty in (t.F32, t.F64) and text.startswith("f64:"):
                value = bytes(int(byte) for byte in text[4:].split(","))
                if len(value) != 8:
                    raise ValueError("float IR constant needs eight binary64 bytes")
                return ir.Const(ty, struct.unpack("<d", value)[0])
            raise ValueError("invalid typed IR constant")
        if kind == "StrLit":
            return ir.StrLit(ty, text)
        if kind == "NoneLit":
            return ir.NoneLit(ty)
        if kind == "LocalGet":
            return ir.LocalGet(ty, text, self.ty(node["declared"]))
        if kind == "FunctionRef":
            return ir.FunctionRef(ty, text)
        if kind == "Closure":
            return ir.Closure(ty, text, args)
        if kind == "CallValue":
            return ir.CallValue(ty, args[0], args[1:])
        if kind == "CellNew":
            return ir.CellNew(ty)
        if kind in ("CellGet", "FreeCellGet"):
            return ir.CellGet(ty, args[0], text, kind == "FreeCellGet")
        if kind == "EnvGet":
            return ir.EnvGet(ty, args[0], self.ty(node["declared"]), int(text))
        if kind == "Unary":
            return ir.Unary(ty, text, args[0])
        if kind == "Binary":
            return ir.Binary(ty, text, *args)
        if kind == "Compare":
            return ir.Compare(ty, text, args[0].ty, *args)
        if kind == "Logical":
            return ir.Logical(ty, text, *args)
        if kind == "StrConcat":
            return ir.StrConcat(ty, *args)
        if kind == "Convert":
            return ir.Convert(ty, args[0])
        if kind == "IsNull":
            return ir.IsNull(ty, args[0], text == "not")
        if kind == "IfExpr":
            return ir.IfExpr(ty, *args, mixed_int=node["mixed"])
        if kind == "Call":
            return ir.Call(ty, text, args)
        if kind == "New":
            return ir.New(ty, self.classes[text], args)
        if kind == "StructNew":
            cls = self.classes[text]
            if len(args) != len(cls.fields):
                raise ValueError("struct initialization must supply every field")
            return ir.StructNew(ty, cls, args)
        if kind == "GetField":
            cls = args[0].ty.info
            return ir.GetField(ty, args[0], cls, cls.fields[text])
        if kind == "CallMethod":
            cls = args[0].ty.info
            return ir.CallMethod(ty, args[0], cls, cls.methods[text], args[1:])
        if kind == "IsInstance":
            return ir.IsInstance(ty, args[0], self.classes[text])
        if kind == "ClassRef":
            return ir.ClassRef(ty, self.classes[text])
        if kind == "TypeName":
            return ir.TypeName(ty, args[0], self.classes[text])
        if kind in ("TypeOf", "ClassName", "TracebackToken"):
            return getattr(ir, kind)(ty, *args)
        if kind == "Await":
            return ir.Await(ty, args[0])
        if kind == "MachineOp":
            return ir.MachineOp(ty, text, args[0], args[1:])
        if kind == "ObjectEq":
            return ir.ObjectEq(ty, *args)
        if kind == "ListLit":
            return ir.ListLit(ty, args)
        if kind == "TupleLit":
            return ir.TupleLit(ty, args)
        if kind == "DictLit":
            if len(args) % 2:
                raise ValueError("dictionary IR needs key/value pairs")
            return ir.DictLit(ty, list(zip(args[::2], args[1::2])))
        if kind in ("ListGet", "ListLen", "ListPop", "ListAppend"):
            return getattr(ir, kind)(ty, *args)
        if kind == "TupleGet":
            return ir.TupleGet(ty, args[0], int(text))
        if kind in ("ListOp", "TupleOp", "DictOp") and args:
            return getattr(ir, kind)(ty, text, args[0], args[1:])
        if kind == "KeyedListOp":
            return ir.KeyedListOp(ty, text, args[0], args[1], args[2:])
        if kind == "StrLen":
            return ir.StrLen(ty, args[0])
        if kind == "ToStr":
            return ir.ToStr(ty, args[0])
        if kind == "Seq":
            return ir.Seq(ty, pre, args[0])
        raise ValueError(f"unsupported owned IR expression: {kind}")

    def statement(self, node):
        kind = node["_type"]
        exprs = [self.expression(expr) for expr in node["exprs"]]
        body = [self.statement(stmt) for stmt in node["body"]]
        otherwise = [self.statement(stmt) for stmt in node["otherwise"]]
        step = [self.statement(stmt) for stmt in node["step"]]
        if kind == "SetLocal" and len(exprs) == 1:
            return ir.SetLocal(node["name"], exprs[0])
        if kind in ("CellSet", "CellClear"):
            return getattr(ir, kind)(*exprs)
        if kind == "SetField" and len(exprs) == 2:
            cls = exprs[0].ty.info
            return ir.SetField(exprs[0], cls, cls.fields[node["name"]], exprs[1])
        if kind == "SetIndex" and len(exprs) == 3:
            return ir.SetIndex(*exprs)
        if kind == "TryFinally" and not exprs:
            return ir.Try(body, [], [], otherwise, node["name"])
        if kind == "Try" and not exprs:
            handlers = []
            for handler in node["handlers"]:
                if handler["_type"] != "Handler":
                    raise ValueError("expected owned Handler record")
                classes = None if handler["catches_all"] else [self.classes[name] for name in handler["classes"]]
                handlers.append(ir.Handler(classes, handler["name"] or None,
                                           self.ty(handler["declared"]) if handler["name"] else None,
                                           [self.statement(s) for s in handler["body"]]))
            final = [self.statement(s) for s in node["finalbody"]] if node["has_final"] else None
            return ir.Try(body, handlers, otherwise, final, node["name"])
        if kind == "Reraise" and not exprs:
            return ir.Raise(None, node["name"])
        if kind == "ExprStmt" and len(exprs) == 1:
            return ir.ExprStmt(exprs[0])
        if kind == "Return" and len(exprs) <= 1:
            return ir.Return(exprs[0] if exprs else None)
        if kind == "If" and len(exprs) == 1:
            return ir.If(exprs[0], body, otherwise)
        if kind == "Loop" and len(exprs) <= 1:
            return ir.Loop(exprs[0] if exprs else None, body, step)
        if kind == "Break":
            return ir.Break()
        if kind == "Continue":
            return ir.Continue()
        if kind == "InlineBlock":
            return ir.InlineBlock(body)
        if kind == "ExitInline":
            return ir.ExitInline()
        if kind == "Yield":
            return ir.Yield(exprs[0])
        if kind == "Print":
            return ir.Print(exprs, node["sep"], node["end"])
        if kind == "Raise" and len(exprs) == 1:
            return ir.Raise(exprs[0])
        if kind == "Pass":
            return ir.If(ir.Const(t.BOOL, True), [], [])
        raise ValueError(f"unsupported/malformed owned IR statement: {kind}")

    def module(self, value):
        if (value.get("_type") != "BodyModule" or value.get("schema") != 7 or value.get("subset") != "language-v7"
                or value.get("body_checking") != "complete-for-subset"):
            raise ValueError("unsupported owned body IR schema/subset")
        from bootstrap.runtime_data import configuration
        if value.get("runtime_abi") != configuration()[2]:
            raise ValueError("checker runtime ABI differs from the current emitter; rebuild the checker")
        functions = self.runtime(value["declarations"])
        self.user_classes(value["declarations"])
        for node in value["functions"]:
            if node["_type"] != "FunctionIR":
                raise ValueError("expected FunctionIR")
            sig = node["signature"]
            params = [(p["name"], self.ty(p["type"])) for p in sig["params"]]
            ret = self.ty(sig["returns"])
            locals_ = {v["name"]: self.ty(v["type"]) for v in node["locals"]}
            body = [self.statement(s) for s in node["body"]]
            main = node["name"] == "snek.main"
            sig_type, self_cast = None, None
            nonnull = set()
            closure_type = self.ty(node["closure_type"])
            if closure_type != t.NONE:
                params.insert(0, ("closure.env", t.EnvT()))
            if node["method_decl"]:
                owner = self.classes[node["owner"]]
                decl = self.classes[node["method_decl"]]
                method_name = node["name"].split(".", 1)[1]
                sig_type = f"{decl.name}.{method_name}.sig"
                params.insert(0, ("self" if owner is decl else "self.in", t.ClassT(decl)))
                if owner is not decl:
                    if "cell.self" not in locals_:
                        self_cast = owner
                        locals_["self"] = t.ClassT(owner)
                        nonnull.add("self")
                    else:
                        locals_.pop("self", None)
            functions.append(ir.Func(node["name"], params, t.CoroutineT(t.NONE) if main else ret,
                                     locals_, body, sig_type=sig_type, self_cast=self_cast,
                                     nonnull_locals=nonnull, machine=main or node["machine"],
                                     closure_type=None if closure_type == t.NONE else closure_type))
        return ir.Module(functions, list(self.classes.values()), "snek.main",
                         UnicodeUse(dynamic=value["unicode_dynamic"], extra=set(value["unicode_extra"]),
                                    flags=value["unicode_flags"], cases=set(value["unicode_cases"]),
                                    decimal=value["unicode_decimal"]))


def to_module(value):
    return IRBridge().module(value)


def check_source(source: str, checker_wasm: Path | None = None) -> dict:
    if checker_wasm is None:
        from bootstrap.build_bodies import check
        return json.loads(check(source).tree().json())
    from snekc import runner
    result = runner.run_binary(checker_wasm, stdin=source.encode("utf-8"))
    if result.code:
        raise ValueError(result.stderr.decode("utf-8", errors="replace").strip())
    if result.stderr:
        raise ValueError("body checker wrote unexpected stderr")
    return json.loads(result.stdout)


def compile_source(source: str, checker_wasm: Path | None = None, *, component: bool = False) -> str:
    from snekc.emit_wat import emit_module
    return emit_module(to_module(check_source(source, checker_wasm)), component=component)


def main() -> int:
    from snekc import driver, runner
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("source", type=Path)
    cli.add_argument("-o", "--out", type=Path, required=True)
    cli.add_argument("--checker-wasm", type=Path, help="run the Snek checker as Wasm instead of using its Python shim")
    cli.add_argument("--no-opt", action="store_true")
    cli.add_argument("--run", action="store_true")
    args = cli.parse_args()
    if args.source.resolve() == args.out.resolve() or (args.checker_wasm and args.checker_wasm.resolve() == args.out.resolve()):
        cli.error("output must not overwrite source or checker")
    try:
        wat = compile_source(args.source.read_text(), args.checker_wasm)
        with tempfile.TemporaryDirectory(prefix="snek-body-program-") as temporary:
            wat_path, binary = Path(temporary, "program.wat"), Path(temporary, "program.wasm")
            wat_path.write_text(wat)
            driver.assemble(wat_path, binary, optimize=not args.no_opt)
            shutil.copyfile(binary, args.out)
        if args.run:
            result = runner.run_binary(args.out, stdin=None)
            sys.stdout.buffer.write(result.stdout)
            sys.stderr.buffer.write(result.stderr)
            return result.code
    except (ValueError, CompileError, driver.ToolError, RuntimeError, OSError) as error:
        print(f"bootstrap checker: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
