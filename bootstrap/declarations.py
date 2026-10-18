"""Owned declaration/type IR and the first checker pass, written in Snek.

Bodies remain syntax trees: success here is NOT a successful type check of a
program. The bootstrap linker supplies the parser and numeric helper imports.
No Python AST, reflection, or Python checker is used by this implementation.
"""
from __future__ import annotations
from snek import i32, i64
from bootstrap.parser import Node, fail, missing, parse_expression, scalar, sequence, string
from bootstrap.encode import float_bytes


def declaration_error(message: str, node: Node) -> None:
    fail(message, node.line, node.column)


def named(node: Node, name: str) -> bool:
    return node.kind == "Name" and node.get("id").text == name


def docstring(node: Node) -> bool:
    return (node.kind == "Expr" and node.get("value").kind == "Constant"
            and node.get("value").get("value").kind == "str")


def main_guard(node: Node) -> bool:
    if node.kind != "If":
        return False
    test = node.get("test")
    if test.kind != "Compare" or not named(test.get("left"), "__name__"):
        return False
    ops = test.get("ops").items
    rhs = test.get("comparators").items
    return (len(ops) == 1 and ops[0].kind == "Eq" and len(rhs) == 1
            and rhs[0].kind == "Constant" and rhs[0].get("value").kind == "str"
            and rhs[0].get("value").text == "__main__")


def direct_yield(node: Node) -> bool:
    if node.kind == "Yield" or node.kind == "YieldFrom":
        return True
    if node.kind in ("FunctionDef", "AsyncFunctionDef", "ClassDef", "Lambda"):
        return False
    for child in node.items:
        if direct_yield(child):
            return True
    return False


def literal(node: Node) -> bool:
    if node.kind == "Constant":
        return node.get("value").kind in ("Number", "str", "scalar", "none")
    if node.kind == "UnaryOp" and node.get("op").kind in ("UAdd", "USub"):
        operand = node.get("operand")
        while operand.kind == "UnaryOp" and operand.get("op").kind in ("UAdd", "USub"):
            operand = operand.get("operand")
        return operand.kind == "Constant" and operand.get("value").kind == "Number"
    return False


def integer_text(text: str, negative: bool, bits: i32, node: Node) -> str:
    """Accumulate negatively so -2**63 never needs an overflowing magnitude."""
    text = text.replace("_", "").lower()
    base = 10
    if text.startswith("0x"):
        base = 16
    elif text.startswith("0o"):
        base = 8
    elif text.startswith("0b"):
        base = 2
    if base != 10:
        text = text[2:]
    limit: i64 = -2147483648 if bits == 32 else -9223372036854775808
    if not negative:
        limit += 1
    value: i64 = 0
    for char in text:
        digit_value = "0123456789abcdef".find(char)
        # ceil((limit + digit) / base), expressed with Python floor division.
        minimum = (limit + digit_value) // base
        if (limit + digit_value) % base != 0:
            minimum += 1
        if value < minimum:
            declaration_error("integer literal does not fit in i" + str(bits), node)
        value = value * base - digit_value
    return str(value if negative else -value)


def floating(text: str) -> bool:
    text = text.lower()
    return not (text.startswith("0x") or text.startswith("0o") or text.startswith("0b")) and ("." in text or "e" in text)


def float_key(text: str) -> str:
    """Binary64 identity of an unsigned Python float token, not decimal spelling.

    The existing exact encoder handles rounding. Decimal order handles infinity
    and underflow before its bounded exponent parser. Very long significands
    remain an explicit bootstrap limit rather than being rounded approximately.
    """
    text = text.lower().replace("_", "")
    pos = text.find("e")
    power = 0
    if pos >= 0:
        exponent = text[pos + 1:]
        text = text[:pos]
        negative = exponent.startswith("-")
        if negative or exponent.startswith("+"):
            exponent = exponent[1:]
        for char in exponent:
            power = min(power * 10 + ord(char) - 48, len(text) + 1000)
        if negative:
            power = -power
    point = text.find(".")
    if point >= 0:
        power -= len(text) - point - 1
        text = text[:point] + text[point + 1:]
    text = text.lstrip("0")
    if len(text) == 0:
        return "0,0,0,0,0,0,0,0"
    order = len(text) + power - 1
    if order > 308:
        return "0,0,0,0,0,0,240,127"
    if order < -324:
        return "0,0,0,0,0,0,0,0"
    stripped = text.rstrip("0")
    power += len(text) - len(stripped)
    if len(stripped) > 3000:
        raise ValueError("bootstrap float defaults support at most 3000 significant digits")
    encoded: list[i32] = []
    try:
        encoded = float_bytes(stripped + "e" + str(power), 64)
    except ValueError as error:
        if str(error) != "floating constant out of range":
            raise
        encoded = [0, 0, 0, 0, 0, 0, 240, 127]
    parts: list[str] = []
    for byte in encoded:
        parts.append(str(byte))
    return ",".join(parts)


def default_key(node: Node) -> str:
    """Match Python AST default equality: ignore origins, retain unary syntax."""
    if node.kind == "Number":
        text = node.get("text").text
        if floating(text):
            return "float:" + float_key(text)
        # An unsigned leaf can be 2**63 inside the valid -2**63 default.
        return "int-magnitude:" + integer_text(text, True, 64, node)
    parts: list[str] = [node.kind, node.text]
    for child in node.items:
        parts.append(default_key(child))
    return sequence_strings(parts).json()


def sequence_strings(values: list[str]) -> Node:
    out: list[Node] = []
    for value in values:
        out.append(string(value))
    return sequence(out)


class DeclType:
    kind: str
    name: str
    args: list[DeclType]

    def __init__(self, kind: str, name: str = "") -> None:
        self.kind = kind
        self.name = name
        self.args = []

    def add(self, arg: DeclType) -> DeclType:
        self.args.append(arg)
        return self

    def reference(self) -> bool:
        return self.kind not in ("i32", "i64", "f32", "f64", "bool", "None")

    def host_boundary(self) -> bool:
        if self.kind == "Callable":
            for i in range(len(self.args)):
                arg = self.args[i]
                if arg.kind == "Callable" or (arg.kind == "None" and i != len(self.args) - 1) or not arg.host_boundary():
                    return False
            return True
        return self.kind in ("i32", "i64", "f32", "f64", "bool", "str", "HostObject", "None") or self.display() == "HostObject | None"

    def display(self) -> str:
        if self.kind == "Stream":
            return "io." + self.name
        if self.kind == "class":
            return self.name
        if self.kind == "optional":
            return self.args[0].display() + " | None"
        parts: list[str] = []
        for arg in self.args:
            parts.append(arg.display())
        if self.kind == "Callable":
            return "Callable[[" + ", ".join(parts[:-1]) + "], " + parts[-1] + "]"
        if self.kind == "Coroutine":
            return "Coroutine[None, None, " + parts[0] + "]"
        if self.kind == "tuple" and len(parts) == 0:
            return "tuple[()]"
        if len(parts) > 0:
            return self.kind + "[" + ", ".join(parts) + "]"
        return self.kind

    def same(self, other: DeclType) -> bool:
        if self.kind != other.kind or self.name != other.name or len(self.args) != len(other.args):
            return False
        for index in range(len(self.args)):
            if not self.args[index].same(other.args[index]):
                return False
        return True

    def hashable(self) -> bool:
        if self.kind in ("str", "bool", "i32", "i64"):
            return True
        if self.kind in ("tuple", "optional"):
            for arg in self.args:
                if not arg.hashable():
                    return False
            return True
        return False

    def tree(self) -> Node:
        args: list[Node] = []
        for arg in self.args:
            args.append(arg.tree())
        return Node("Type").put("kind", string(self.kind)).put("name", string(self.name)).put("args", sequence(args))


class DeclParam:
    name: str
    ty: DeclType
    default: Node

    def __init__(self, name: str, ty: DeclType, default: Node) -> None:
        self.name = name
        self.ty = ty
        self.default = default

    def tree(self) -> Node:
        return Node("Parameter").put("name", string(self.name)).put("type", self.ty.tree()).put("default", self.default)


class DeclSignature:
    params: list[DeclParam]
    ret: DeclType
    host: str

    def __init__(self) -> None:
        self.params = []
        self.ret = DeclType("None")
        self.host = ""

    def same(self, other: DeclSignature) -> bool:
        if not self.ret.same(other.ret) or len(self.params) != len(other.params):
            return False
        for index in range(len(self.params)):
            a = self.params[index]
            b = other.params[index]
            if a.name != b.name or not a.ty.same(b.ty) or default_key(a.default) != default_key(b.default):
                return False
        return True

    def tree(self) -> Node:
        params: list[Node] = []
        for param in self.params:
            params.append(param.tree())
        return Node("Signature").put("params", sequence(params)).put("returns", self.ret.tree()).put("host", string(self.host))


class DeclField:
    name: str
    ty: DeclType
    final: bool
    owner: str
    index: i32
    default: Node

    def __init__(self, name: str, ty: DeclType, final: bool, owner: str, index: i32, default: Node) -> None:
        self.name = name
        self.ty = ty
        self.final = final
        self.owner = owner
        self.index = index
        self.default = default

    def tree(self) -> Node:
        return (Node("Field").put("name", string(self.name)).put("type", self.ty.tree())
                .put("final", Node("scalar", "true" if self.final else "false"))
                .put("owner", string(self.owner)).put("index", scalar(self.index)).put("default", self.default))


class DeclMethod:
    name: str
    sig: DeclSignature
    decl: str
    owner: str
    slot: i32
    node: Node

    def __init__(self, name: str, sig: DeclSignature, decl: str, owner: str, slot: i32, node: Node) -> None:
        self.name = name
        self.sig = sig
        self.decl = decl
        self.owner = owner
        self.slot = slot
        self.node = node

    def tree(self) -> Node:
        return (Node("Method").put("name", string(self.name)).put("signature", self.sig.tree())
                .put("decl", string(self.decl)).put("owner", string(self.owner)).put("slot", scalar(self.slot)))


class DeclClass:
    name: str
    node: Node
    builtin: bool
    base: str
    final: bool
    fields: dict[str, DeclField]
    methods: dict[str, DeclMethod]
    init_node: Node
    constructor: DeclSignature

    def __init__(self, node: Node, builtin: bool) -> None:
        self.name = node.get("name").text
        self.node = node
        self.builtin = builtin
        self.base = ""
        self.final = False
        self.fields = {}
        self.methods = {}
        self.init_node = missing()
        self.constructor = DeclSignature()

    def tree(self) -> Node:
        fields: list[Node] = []
        methods: list[Node] = []
        for name in self.fields:
            fields.append(self.fields[name].tree())
        for name in self.methods:
            methods.append(self.methods[name].tree())
        return (Node("Class").put("name", string(self.name)).put("base", string(self.base))
                .put("builtin", Node("scalar", "true" if self.builtin else "false"))
                .put("final", Node("scalar", "true" if self.final else "false"))
                .put("fields", sequence(fields)).put("methods", sequence(methods))
                .put("constructor", self.constructor.tree()))


class Declarations:
    classes: dict[str, DeclClass]
    class_order: list[str]
    functions: dict[str, DeclSignature]
    function_nodes: dict[str, Node]
    imported: list[str]
    library_imports: dict[str, list[str]]
    main: list[Node]
    reserved: list[str]
    libraries: dict[str, list[str]]

    def __init__(self, helpers: list[str], libraries: dict[str, list[str]]) -> None:
        self.classes = {}
        self.class_order = []
        self.functions = {}
        self.function_nodes = {}
        self.imported = []
        self.library_imports = {}
        self.main = []
        self.reserved = ["print", "len", "str", "range", "isinstance", "abs", "min", "max", "sum", "sorted",
                         "bool", "type", "i32", "i64", "f32", "f64", "int", "float", "list", "dict", "tuple",
                         "object", "super", "ord", "chr", "repr", "iter", "next", "u8s", "rt", "lit", "snek"]
        self.libraries = libraries
        self.reserved.extend(helpers)

    def new_name(self, name: str, node: Node) -> None:
        if name in self.classes and self.classes[name].builtin:
            declaration_error("'" + name + "' is a built-in exception class and cannot be redefined", node)
        if name in self.classes or name in self.function_nodes:
            declaration_error("'" + name + "' is already defined", node)
        if (name in self.reserved or name in self.library_imports
                or (name in ("host", "run", "checkpoint", "read_bytes", "write_bytes") and name in self.imported)):
            declaration_error("'" + name + "' is a reserved name", node)

    def library_binding(self, name: str, path: list[str], node: Node) -> None:
        if name in self.function_nodes or name in self.classes or name in self.imported:
            declaration_error("'" + name + "' is already defined", node)
        if name in self.reserved and name != "snek":
            declaration_error("'" + name + "' is a reserved name", node)
        if name in self.library_imports and self.library_imports[name] != path:
            declaration_error("'" + name + "' is already imported", node)
        self.library_imports[name] = path

    def imports(self, node: Node) -> None:
        # As in the production namespace resolver, the last alias within one
        # import wins; a conflicting alias in a later statement is an error.
        bindings: dict[str, list[str]] = {}
        if node.kind == "Import":
            for alias in node.get("names").items:
                module = alias.get("name").text
                if not module.startswith("snek.") or module[5:] not in self.libraries:
                    declaration_error("plain `import` is not supported; use `from snek import i32, ...`", node)
                local = alias.get("asname")
                path: list[str] = []
                name = "snek"
                if local.kind != "none":
                    name = local.text
                    path.append(module[5:])
                bindings[name] = path
            for name in bindings:
                self.library_binding(name, bindings[name], node)
            return
        module = node.get("module").text
        level = node.get("level").text
        allowed: list[str] = []
        if module == "snek":
            allowed = ["i32", "i64", "f32", "f64", "HostObject", "Final", "final", "host", "run", "checkpoint", "read_bytes", "write_bytes"]
        elif module in ("typing", "collections.abc"):
            allowed = ["Iterator", "Generator", "Coroutine", "Callable"]
            if module == "typing":
                allowed.extend(["Final", "Optional", "final"])
        elif module == "types":
            allowed = ["TracebackType"]
        elif module == "__future__":
            allowed = ["annotations"]
        elif not (module.startswith("snek.") and module[5:] in self.libraries):
            declaration_error("import from '" + module + "' is not supported (only snek, typing, types and __future__)", node)
        for alias in node.get("names").items:
            name = alias.get("name").text
            local = alias.get("asname")
            binding = name if local.kind == "none" else local.text
            if module == "snek" and name in self.libraries and level == "0":
                bindings[binding] = [name]
            elif module.startswith("snek.") and module[5:] in self.libraries and level == "0":
                if name not in self.libraries[module[5:]]:
                    declaration_error("cannot import '" + name + "' from " + module, node)
                bindings[binding] = [module[5:], name]
        for name in bindings:
            self.library_binding(name, bindings[name], node)
        for alias in node.get("names").items:
            name = alias.get("name").text
            local = alias.get("asname")
            if level == "0" and ((module == "snek" and name in self.libraries)
                                  or (module.startswith("snek.") and module[5:] in self.libraries)):
                continue
            if name not in allowed or local.kind != "none" or level != "0":
                declaration_error("cannot import '" + name + "' from " + module, node)
            if name in self.library_imports:
                declaration_error("'" + name + "' is already imported", node)
            if name in ("host", "run", "checkpoint", "read_bytes", "write_bytes") and (name in self.classes or name in self.function_nodes):
                declaration_error("'" + name + "' is already defined", node)
            if name not in self.imported:
                self.imported.append(name)

    def run(self, module: Node, builtins: Node) -> None:
        for node in builtins.get("body").items:
            if node.kind != "ClassDef":
                declaration_error("bootstrap builtins must contain class declarations", node)
            cls = DeclClass(node, True)
            self.classes[cls.name] = cls
        for node in module.get("body").items:
            if node.kind in ("Import", "ImportFrom"):
                self.imports(node)
            elif node.kind in ("ClassDef", "FunctionDef", "AsyncFunctionDef"):
                name = node.get("name").text
                self.new_name(name, node)
                if node.kind == "ClassDef":
                    self.classes[name] = DeclClass(node, False)
                else:
                    self.function_nodes[name] = node
            elif main_guard(node):
                if len(node.get("orelse").items) > 0:
                    declaration_error("else branch on the __main__ guard is not supported", node)
                self.main.extend(node.get("body").items)
            elif docstring(node):
                pass
            elif node.kind in ("Expr", "If", "While", "For", "Try", "With", "Raise", "Assert"):
                self.main.append(node)
            elif node.kind in ("Assign", "AnnAssign", "AugAssign"):
                declaration_error("module-level variables are not supported yet; keep state in locals or objects", node)
            else:
                declaration_error("unsupported top-level statement: " + node.kind, node)
        self.hierarchy()
        for name in self.class_order:
            self.members(self.classes[name])
        for name in self.function_nodes:
            self.functions[name] = self.signature(self.function_nodes[name], False, True)
        # Constructors are resolved separately, as in the production checker.
        for name in self.class_order:
            cls = self.classes[name]
            if cls.init_node.kind != "none":
                cls.constructor = self.signature(cls.init_node, True)
            elif cls.base != "":
                cls.constructor = self.classes[cls.base].constructor

    def hierarchy(self) -> None:
        for name in self.classes:
            cls = self.classes[name]
            node = cls.node
            if len(node.get("keywords").items) > 0:
                declaration_error("class keywords (metaclass=...) are not supported", node)
            for decorator in node.get("decorator_list").items:
                if not named(decorator, "final"):
                    declaration_error("only the @final decorator is supported on classes", decorator)
                cls.final = True
            bases = node.get("bases").items
            if len(bases) > 1:
                declaration_error("multiple inheritance is not supported (WasmGC structs have a single supertype); use one base class", bases[1])
            if len(bases) > 0 and not named(bases[0], "object"):
                if bases[0].kind != "Name" or bases[0].get("id").text not in self.classes:
                    declaration_error("base class must be a class defined in this module or a built-in exception class", bases[0])
                cls.base = bases[0].get("id").text
        state: dict[str, i32] = {}
        for name in self.classes:
            self.visit_class(name, state)
        for name in self.class_order:
            cls = self.classes[name]
            if cls.base != "" and self.classes[cls.base].final:
                declaration_error("cannot subclass @final class '" + cls.base + "'", cls.node.get("bases").items[0])

    def visit_class(self, name: str, state: dict[str, i32]) -> None:
        mark = state.get(name, 0)
        if mark == 2:
            return
        cls = self.classes[name]
        if mark == 1:
            declaration_error("inheritance cycle involving '" + name + "'", cls.node)
        state[name] = 1
        if cls.base != "":
            self.visit_class(cls.base, state)
        state[name] = 2
        self.class_order.append(name)

    def subclass(self, name: str, base: str) -> bool:
        while name != "":
            if name == base:
                return True
            name = self.classes[name].base
        return False

    def exception_attr(self, cls: DeclClass, name: str, node: Node) -> None:
        if name in ("args", "with_traceback", "add_note") and self.subclass(cls.name, "BaseException"):
            declaration_error("'" + name + "' is reserved on exception classes (it is a BaseException attribute in CPython, and snek does not model it)", node)

    def members(self, cls: DeclClass) -> None:
        if cls.base != "":
            cls.fields = self.classes[cls.base].fields.copy()
            cls.methods = self.classes[cls.base].methods.copy()
        own: list[str] = []
        for node in cls.node.get("body").items:
            if docstring(node) or node.kind == "Pass":
                continue
            if node.kind == "AnnAssign" and node.get("target").kind == "Name":
                self.field(cls, node)
            elif node.kind in ("FunctionDef", "AsyncFunctionDef"):
                if len(node.get("decorator_list").items) > 0:
                    declaration_error("method decorators (@staticmethod, @property, ...) are not supported yet", node.get("decorator_list").items[0])
                name = node.get("name").text
                if name in own:
                    declaration_error("method '" + name + "' is defined twice", node)
                own.append(name)
                if name == "__init__":
                    cls.init_node = node
                    continue
                if name.startswith("__") and name.endswith("__") and name not in ("__eq__", "__str__", "__len__", "__enter__", "__exit__"):
                    declaration_error("special method " + name + " is not supported yet", node)
                if name in cls.fields:
                    declaration_error("'" + name + "' is already a field of " + cls.name, node)
                self.exception_attr(cls, name, node)
                sig = self.signature(node, True)
                self.special(cls, node, sig)
                if name in cls.methods:
                    inherited = cls.methods[name]
                    if not sig.same(inherited.sig):
                        declaration_error(cls.name + "." + name + " overrides " + inherited.decl + "." + name
                                          + " with a different signature; overrides must keep the same parameter names, types, defaults and return type", node)
                    cls.methods[name] = DeclMethod(name, inherited.sig, inherited.decl, cls.name, inherited.slot, node)
                else:
                    cls.methods[name] = DeclMethod(name, sig, cls.name, cls.name, len(cls.methods), node)
            elif node.kind == "Assign":
                declaration_error("class attributes need a type annotation (they become instance fields): `name: T = value`", node)
            else:
                declaration_error("unsupported statement in class body: " + node.kind, node)

    def special(self, cls: DeclClass, node: Node, sig: DeclSignature) -> None:
        name = node.get("name").text
        if name not in ("__eq__", "__str__", "__len__", "__enter__", "__exit__"):
            return
        valid = True
        for param in sig.params:
            if param.default.kind != "none":
                valid = False
        want = ""
        if name == "__eq__":
            other = DeclType("None")
            if len(sig.params) == 1:
                other = sig.params[0].ty
                if other.kind == "optional":
                    other = other.args[0]
            valid = valid and other.kind == "class" and self.subclass(cls.name, other.name) and sig.ret.kind == "bool"
            want = "one other parameter typed as this class (or a base, optionally | None), and -> bool"
        elif name == "__str__":
            valid = valid and len(sig.params) == 0 and sig.ret.kind == "str"
            want = "no parameters beyond self, and -> str"
        elif name == "__len__":
            valid = valid and len(sig.params) == 0 and sig.ret.kind in ("i32", "i64")
            want = "no parameters beyond self, and -> i32 or -> i64"
        elif name == "__enter__":
            valid = valid and len(sig.params) == 0
            want = "no parameters beyond self"
        else:
            params: list[str] = []
            for param in sig.params:
                params.append(param.ty.display())
            valid = valid and params == ["type[BaseException] | None", "BaseException | None", "TracebackType | None"] and sig.ret.kind in ("bool", "None")
            want = "(self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None) -> bool or -> None (from types import TracebackType)"
        if not valid:
            declaration_error(name + " requires " + want + "; default arguments are not supported", node)

    def field(self, cls: DeclClass, node: Node) -> None:
        name = node.get("target").get("id").text
        ann = node.get("annotation")
        final = False
        if ann.kind == "Subscript" and named(ann.get("value"), "Final"):
            final = True
            ann = ann.get("slice")
        elif named(ann, "Final"):
            declaration_error("Final fields need an explicit type: Final[T]", ann)
        ty = self.resolve_type(ann)
        if ty.kind == "None":
            declaration_error("a field cannot have type None", ann)
        if name.startswith("__") and not cls.builtin:
            declaration_error("field names starting with '__' are reserved", node)
        self.exception_attr(cls, name, node)
        if name in cls.fields:
            declaration_error("field '" + name + "' is already declared in " + cls.fields[name].owner, node)
        default = node.get("value")
        if default.kind != "none" and not literal(default):
            declaration_error("field defaults must be literals", default)
        cls.fields[name] = DeclField(name, ty, final, cls.name, len(cls.fields) + 1, default)

    def signature(self, node: Node, method: bool, allow_host: bool = False) -> DeclSignature:
        arguments = node.get("args")
        if (arguments.get("vararg").kind != "none" or arguments.get("kwarg").kind != "none"
                or len(arguments.get("kwonlyargs").items) > 0 or len(arguments.get("posonlyargs").items) > 0):
            declaration_error("*args, **kwargs, keyword-only and positional-only parameters are not supported yet", node)
        host = ""
        decorators = node.get("decorator_list").items
        if not method and len(decorators) > 0:
            decorator = decorators[0]
            if (not allow_host or len(decorators) != 1 or decorator.kind != "Call"
                    or not named(decorator.get("func"), "host") or "host" not in self.imported):
                declaration_error('function decorators support only top-level @host("name") declarations', decorator)
            values = decorator.get("args").items
            if (len(values) != 1 or len(decorator.get("keywords").items) != 0
                    or values[0].kind != "Constant" or values[0].get("value").kind != "str"):
                declaration_error('@host requires one literal string name', decorator)
            host = values[0].get("value").text
            if len(host) == 0:
                declaration_error("host import name must not be empty", decorator)
            if not (host[0].isascii() and (host[0].isalpha() or host[0] == "_")):
                declaration_error("host import names must be ASCII identifiers", decorator)
            for char in host:
                if not (char.isascii() and (char.isalnum() or char == "_")):
                    declaration_error("host import names must be ASCII identifiers", decorator)
            body = node.get("body").items
            if len(body) > 0 and docstring(body[0]): body = body[1:]
            if len(body) != 1 or body[0].kind != "Pass":
                declaration_error("host declarations must have only a pass body (plus an optional docstring)", node)
            if len(arguments.get("defaults").items) != 0:
                declaration_error("host declarations cannot have default arguments", node)
        args = arguments.get("args").items
        defaults = arguments.get("defaults").items
        offset = len(args) - len(defaults)
        start = 0
        if method:
            if len(args) == 0 or args[0].get("arg").text != "self":
                declaration_error("methods must take `self` as their first parameter", node)
            if offset == 0:
                declaration_error("`self` cannot have a default value", defaults[0])
            start = 1
        sig = DeclSignature()
        sig.host = host
        seen: list[str] = ["self"] if method else []
        for index in range(start, len(args)):
            arg = args[index]
            name = arg.get("arg").text
            if name in seen:
                declaration_error("duplicate parameter '" + name + "'", arg)
            seen.append(name)
            ann = arg.get("annotation")
            if ann.kind == "none":
                declaration_error("parameter '" + name + "' needs a type annotation", arg)
            ty = self.resolve_type(ann)
            if ty.kind == "None":
                declaration_error("a parameter cannot have type None", ann)
            default = missing() if index < offset else defaults[index - offset]
            if default.kind != "none":
                self.check_default(default, ty, name)
            sig.params.append(DeclParam(name, ty, default))
        ret = node.get("returns")
        if ret.kind == "none":
            if node.get("name").text != "__init__":
                declaration_error("'" + node.get("name").text + "' needs a return annotation (`-> T` or `-> None`)", node)
        else:
            sig.ret = self.resolve_type(ret)
        if host != "":
            for param in sig.params:
                if not param.ty.host_boundary():
                    declaration_error("host boundaries require scalar, string, HostObject, or supported Callable parameters", node)
            if not sig.ret.host_boundary():
                declaration_error("host boundaries require scalar, string, HostObject, supported Callable, or None returns", node)
        yields = direct_yield(node.get("body"))
        if node.kind == "AsyncFunctionDef":
            if yields:
                declaration_error("async generators are not supported yet", node)
            if node.get("name").text.startswith("__"):
                declaration_error("async special methods are not supported yet", node)
            sig.ret = DeclType("Coroutine").add(sig.ret)
        elif yields:
            if node.get("name").text.startswith("__"):
                declaration_error("generator special methods are not supported", node)
            if sig.ret.kind != "Iterator":
                declaration_error("a generator needs an Iterator[T] or Generator[T, None, None] return annotation", node)
        return sig

    def check_default(self, node: Node, ty: DeclType, name: str) -> None:
        if not literal(node):
            declaration_error("default values must be literals (numbers, strings, bools or None); CPython evaluates defaults once, so anything mutable would be shared", node)
        value = node
        negative = False
        while value.kind == "UnaryOp":
            if value.get("op").kind == "USub":
                negative = not negative
            value = value.get("operand")
        value = value.get("value")
        got = value.kind
        if got == "scalar":
            got = "bool"
        if got == "Number":
            text = value.get("text").text
            if floating(text):
                got = "float"
            else:
                got = "int"
                integer_text(text, negative, 32 if ty.kind == "i32" else 64, node)
        target = ty.kind
        if target == "optional":
            if got == "none":
                return
            target = ty.args[0].kind
        if (got == target or (got == "int" and target in ("i32", "i64"))
                or (got == "float" and target in ("f32", "f64"))):
            return
        declaration_error("default for '" + name + "': expected " + ty.display() + ", got " + got, node)

    def optional(self, ty: DeclType, node: Node) -> DeclType:
        if ty.kind == "optional":
            return ty
        if not ty.reference():
            declaration_error("'" + ty.display() + " | None' is not allowed: only reference types (str, containers, classes) can be None, because numbers are never boxed", node)
        return DeclType("optional").add(ty)

    def resolve_type(self, node: Node, depth: i32 = 0) -> DeclType:
        if depth > 200:
            declaration_error("type annotation nesting exceeds bootstrap limit (200)", node)
        root = node
        path: list[str] = []
        while root.kind == "Attribute":
            path.insert(0, root.get("attr").text)
            root = root.get("value")
        if root.kind == "Name" and root.get("id").text in self.library_imports:
            path = self.library_imports[root.get("id").text] + path
            if len(path) == 2 and path[0] == "io" and path[1] in ("TextInput", "TextOutput", "BinaryInput", "BinaryOutput"):
                return DeclType("Stream", path[1])
        if node.kind == "Constant":
            value = node.get("value")
            if value.kind == "none":
                return DeclType("None")
            if value.kind == "str":
                inner = missing()
                try:
                    inner = parse_expression(value.text).get("body")
                except ValueError as error:
                    declaration_error("invalid quoted type annotation: " + str(error), node)
                self.relocate(inner, node)
                return self.resolve_type(inner, depth + 1)
        if node.kind == "Name":
            name = node.get("id").text
            if name == "HostObject" and name in self.imported:
                return DeclType(name)
            if name in ("i32", "i64", "f32", "f64"):
                if name not in self.imported:
                    declaration_error("'" + name + "' must be imported: `from snek import " + name + "`", node)
                return DeclType(name)
            if name in ("bool", "str") or (name == "TracebackType" and name in self.imported):
                return DeclType(name)
            if name == "int":
                declaration_error("use a fixed-width integer type (i32 or i64) instead of int", node)
            if name == "float":
                declaration_error("use f64 (or f32) instead of float", node)
            if name == "list":
                declaration_error("list needs an element type: list[T]", node)
            if name in ("dict", "tuple"):
                declaration_error(name + " needs type arguments: dict[K, V] or tuple[T, ...] (fixed length)", node)
            if name in self.classes:
                return DeclType("class", name)
            declaration_error("unknown type '" + name + "'", node)
        if node.kind == "BinOp" and node.get("op").kind == "BitOr":
            left = self.resolve_type(node.get("left"), depth + 1)
            right = self.resolve_type(node.get("right"), depth + 1)
            if left.kind == "None":
                return self.optional(right, node)
            if right.kind == "None":
                return self.optional(left, node)
            declaration_error("only `T | None` unions are supported", node)
        if node.kind == "Subscript" and node.get("value").kind == "Name":
            name = node.get("value").get("id").text
            value = node.get("slice")
            args: list[Node] = value.get("elts").items if value.kind == "Tuple" else [value]
            if name in ("Callable", "Iterator", "Generator", "Coroutine") and name not in self.imported:
                declaration_error("'" + name + "' must be imported from typing or collections.abc", node)
            if name == "Callable":
                if len(args) != 2 or args[0].kind != "List":
                    declaration_error("use Callable[[T, ...], R] with explicit parameter types", node)
                callable = DeclType("Callable")
                for arg in args[0].get("elts").items:
                    param = self.resolve_type(arg, depth + 1)
                    if param.kind == "None":
                        declaration_error("a Callable parameter cannot have type None", node)
                    callable.add(param)
                return callable.add(self.resolve_type(args[1], depth + 1))
            if name in ("Iterator", "Generator", "Coroutine"):
                types: list[DeclType] = []
                for arg in args:
                    types.append(self.resolve_type(arg, depth + 1))
                if name == "Iterator" and len(types) == 1 and types[0].kind != "None":
                    return DeclType("Iterator").add(types[0])
                if name == "Generator" and len(types) == 3 and types[0].kind != "None" and types[1].kind == "None" and types[2].kind == "None":
                    return DeclType("Iterator").add(types[0])
                if name == "Coroutine" and len(types) == 3 and types[0].kind == "None" and types[1].kind == "None":
                    return DeclType("Coroutine").add(types[2])
                declaration_error("use Iterator[T], Generator[T, None, None], or Coroutine[None, None, T]", node)
            if name in ("list", "type", "Optional"):
                inner_type = self.resolve_type(value, depth + 1)
                if name == "Optional":
                    return self.optional(inner_type, node)
                if name == "type" and inner_type.kind != "class":
                    declaration_error("type[T] needs a class type", node)
                if name == "list" and inner_type.kind == "None":
                    declaration_error("list[None] is not a useful type", node)
                return DeclType(name).add(inner_type)
            if name == "dict":
                if len(args) != 2:
                    declaration_error("dict needs two types: dict[K, V]", node)
                key = self.resolve_type(args[0], depth + 1)
                value_type = self.resolve_type(args[1], depth + 1)
                if not key.hashable():
                    declaration_error("unsupported dictionary key type " + key.display() + "; use str, bool, i32, i64, or tuples of supported keys (optionally None for reference keys)", node)
                if value_type.kind == "None":
                    declaration_error("dict values need a concrete type; use T | None for optional references", node)
                return DeclType("dict").add(key).add(value_type)
            if name == "tuple":
                result = DeclType("tuple")
                for arg in args:
                    if arg.kind == "Constant" and arg.get("value").kind == "Ellipsis":
                        declaration_error("tuples have fixed length; tuple[T, ...] is not supported", node)
                    elem = self.resolve_type(arg, depth + 1)
                    if elem.kind == "None":
                        declaration_error("tuple elements need a concrete type; use T | None for optional references", node)
                    result.add(elem)
                return result
            if name == "Final":
                declaration_error("Final is only allowed on class fields", node)
            if name == "set":
                declaration_error("set is not supported yet", node)
        declaration_error("unsupported type annotation", node)
        return DeclType("None")

    def relocate(self, node: Node, origin: Node) -> None:
        node.line = origin.line
        node.column = origin.column
        for child in node.items:
            self.relocate(child, origin)

    def tree(self) -> Node:
        classes: list[Node] = []
        functions: list[Node] = []
        imports: list[Node] = []
        for name in self.class_order:
            classes.append(self.classes[name].tree())
        for name in self.functions:
            functions.append(Node("Function").put("name", string(name)).put("signature", self.functions[name].tree()))
        for name in self.library_imports:
            imports.append(Node("ImportBinding").put("name", string(name)).put("path", sequence_strings(self.library_imports[name])))
        return (Node("Declarations").put("schema", scalar(1)).put("body_checking", string("pending"))
                .put("classes", sequence(classes)).put("functions", sequence(functions))
                .put("imported", sequence_strings(sorted(self.imported))).put("library_imports", sequence(imports))
                .put("main_statements", scalar(len(self.main))))
