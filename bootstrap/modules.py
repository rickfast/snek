"""Static local-module linking, shared by the reference and self-hosted compilers.

The host supplies UTF-8 source files; this pass owns parsing, imports, lexical
namespaces and initialization order. No filesystem or Python AST dependency.
"""
from __future__ import annotations
from snek import i32
from bootstrap.parser import Node, parse, parse_expression, string, scalar, sequence, missing, fail
from bootstrap.declarations import main_guard, docstring
from bootstrap.scopes import scope_walk, scope_target


def module_set(node: Node, key: str, value: Node) -> None:
    for i in range(len(node.keys)):
        if node.keys[i] == key:
            node.items[i] = value
            return
    node.put(key, value)


def module_copy(node: Node) -> Node:
    out = Node(node.kind, node.text, node.line, node.column)
    out.keys = node.keys.copy()
    for item in node.items:
        out.items.append(module_copy(item))
    return out


def module_offset(node: Node, offset: i32) -> None:
    if node.line > 0:
        node.line += offset
    for item in node.items:
        module_offset(item, offset)


def module_name(name: str, origin: Node) -> Node:
    return Node("Name", "", origin.line, origin.column).put("id", string(name))


def module_builtin(name: str) -> bool:
    return name.split(".")[0] in ("snek", "typing", "collections", "types", "__future__")


class SourceModule:
    name: str
    path: str
    package: bool
    present: bool
    tree: Node
    bindings: dict[str, str]
    state: i32

    def __init__(self, name: str, path: str, package: bool, present: bool, source: str, offset: i32) -> None:
        self.name = name
        self.path = path
        self.package = package
        self.present = present
        self.tree = parse(source)
        self.bindings = {}
        self.state = 0
        module_offset(self.tree, offset)


class ModuleStatement:
    owner: str
    node: Node

    def __init__(self, owner: str, node: Node) -> None:
        self.owner = owner
        self.node = node


class ModuleProject:
    entry: str
    library: bool
    modules: dict[str, SourceModule]
    statements: list[ModuleStatement]
    imports: dict[str, Node]
    stack: list[str]

    def __init__(self, entry: str) -> None:
        self.entry = entry
        self.library = False
        self.modules = {}
        self.statements = []
        self.imports = {}
        self.stack = []

    def error(self, message: str, node: Node) -> None:
        fail(message, node.line, node.column + 1)

    def need(self, name: str, node: Node, optional: bool = False) -> SourceModule:
        if name not in self.modules:
            raise ValueError("SNEK-MODULE-NEEDED " + name)
        module = self.modules[name]
        if not module.present and not optional:
            self.error("local module '" + name + "' not found under the module root", node)
        return module

    def absolute(self, owner: SourceModule, name: str, level: i32, node: Node) -> str:
        if level == 0:
            return name
        parts = owner.name.split(".")
        if not owner.package:
            parts = parts[:-1]
        if level > len(parts):
            self.error("relative import escapes the package; use --module-root for a package entry point", node)
        base = ".".join(parts[:len(parts) - level + 1])
        return base + "." + name if name != "" else base

    def bind(self, owner: SourceModule, local: str, target: str, node: Node) -> None:
        if local in owner.bindings and owner.bindings[local] != target:
            self.error("module name '" + local + "' is already bound", node)
        owner.bindings[local] = target

    def builtin(self, name: str, node: Node) -> str:
        parts = name.split(".")
        if len(parts) == 2 and parts[0] == "snek" and parts[1] in ("sys", "io", "os", "time", "secrets"):
            alias = "@library." + parts[1]
            if alias not in self.imports:
                item = Node("alias").put("name", string(parts[1])).put("asname", string(alias))
                self.imports[alias] = Node("ImportFrom", "", node.line, node.column).put("module", string("snek")).put("names", sequence([item])).put("level", scalar(0))
            return alias
        if len(parts) == 3 and parts[0] == "snek":
            key = "validate:" + name
            item = Node("alias").put("name", string(parts[-1])).put("asname", string("@validate." + name))
            self.imports[key] = Node("ImportFrom", "", node.line, node.column).put("module", string(".".join(parts[:-1]))).put("names", sequence([item])).put("level", scalar(0))
            return self.builtin(".".join(parts[:2]), node) + "." + parts[2]
        if len(parts) != 2 and not (len(parts) == 3 and parts[0] == "collections" and parts[1] == "abc"):
            self.error("unsupported standard-library import '" + name + "'", node)
        alias = parts[-1]
        key = "builtin:" + name
        if key not in self.imports:
            item = Node("alias").put("name", string(alias)).put("asname", missing())
            self.imports[key] = Node("ImportFrom", "", node.line, node.column).put("module", string(".".join(parts[:-1]))).put("names", sequence([item])).put("level", scalar(0))
        return alias

    def load(self, name: str, origin: Node) -> SourceModule:
        module = self.need(name, origin)
        if module.state == 1:
            self.error("cyclic module import: " + " -> ".join(self.stack + [name]), origin)
        if module.state == 2:
            return module
        # Import a package before its child, as Python does. A parent currently
        # importing its child is not itself a cycle.
        parts = name.split(".")
        if len(parts) > 1:
            parent_name = ".".join(parts[:-1])
            parent = self.need(parent_name, origin)
            if not parent.package:
                self.error("'" + parent_name + "' is not a package", origin)
            if parent.state != 1:
                self.load(parent_name, origin)
            if module.state == 2:
                return module
            self.bind(parent, parts[-1], "@module:" + name, origin)
        module.state = 1
        self.stack.append(name)
        for node in module.tree.get("body").items:
            if node.kind in ("FunctionDef", "AsyncFunctionDef", "ClassDef"):
                local = node.get("name").text
                if local in module.bindings:
                    self.error("module name '" + local + "' is already defined", node)
                module.bindings[local] = "@user:" + name.replace(".", "~") + ":" + local
        for node in module.tree.get("body").items:
            if node.kind in ("Import", "ImportFrom"):
                self.import_statement(module, node)
            elif main_guard(node):
                branch = "body" if name == self.entry and not self.library else "orelse"
                for child in node.get(branch).items:
                    if child.kind in ("FunctionDef", "AsyncFunctionDef", "ClassDef"):
                        self.error("module functions and classes must be declared directly at module scope", child)
                    self.statements.append(ModuleStatement(name, child))
            else:
                self.statements.append(ModuleStatement(name, node))
        self.stack.pop()
        module.state = 2
        return module

    def import_statement(self, owner: SourceModule, node: Node) -> None:
        if node.kind == "Import":
            for alias in node.get("names").items:
                name = alias.get("name").text
                local = alias.get("asname").text
                if module_builtin(name):
                    if name not in ("snek", "snek.sys", "snek.io", "snek.os", "snek.time", "snek.secrets", "typing", "types", "collections", "collections.abc", "__future__"):
                        self.error("unsupported standard-library module '" + name + "'", node)
                    target = "@builtin:" + name
                else:
                    self.load(name, node)
                    target = "@module:" + name
                if local == "":
                    local = name.split(".")[0]
                    target = ("@builtin:" if module_builtin(name) else "@module:") + local
                self.bind(owner, local, target, node)
            return
        name = node.get("module").text
        level = i32(node.get("level").text)
        name = self.absolute(owner, name, level, node)
        for alias in node.get("names").items:
            member = alias.get("name").text
            if member == "*":
                self.error("wildcard imports are not supported; import names explicitly", node)
            local = alias.get("asname").text
            if local == "": local = member
            if module_builtin(name):
                if name == "__future__":
                    self.builtin(name + "." + member, node)
                    continue
                target = "@builtin:" + name + "." + member
                # Validate imports even when unused.
                self.builtin(name + "." + member, node)
            else:
                module = self.need(name, node)
                # A child can import a sibling from its initializing package.
                if not (module.package and module.state == 1 and (owner.name == name or owner.name.startswith(name + "."))):
                    module = self.load(name, node)
                if member not in module.bindings:
                    candidate = name + "." + member
                    child = self.need(candidate, node, True) if module.package else module
                    if not module.package or not child.present:
                        self.error("cannot import '" + member + "' from '" + name + "'", node)
                    self.load(candidate, node)
                target = module.bindings[member]
            self.bind(owner, local, target, node)

    def resolve(self, owner: SourceModule, node: Node, locals_: list[str]) -> str:
        if node.kind == "Name":
            name = node.get("id").text
            if name in locals_:
                return ""
            return owner.bindings.get(name, "")
        if node.kind != "Attribute":
            return ""
        base = self.resolve(owner, node.get("value"), locals_)
        attr = node.get("attr").text
        if base.startswith("@builtin:"):
            if base[9:] in ("snek", "snek.sys", "snek.io", "snek.os", "snek.time", "snek.secrets", "typing", "types", "collections", "collections.abc"):
                return base + "." + attr
            return ""
        if base.startswith("@module:"):
            module = self.modules[base[8:]]
            if attr not in module.bindings:
                self.error("module '" + module.name + "' has no exported name '" + attr + "'", node)
            return module.bindings[attr]
        return ""

    def value(self, target: str, node: Node) -> Node:
        if target.startswith("@module:"):
            self.error("modules are static namespaces, not runtime values", node)
        if target.startswith("@builtin:"):
            target = self.builtin(target[9:], node)
        if target.startswith("@library."):
            parts = target.split(".")
            out = module_name(".".join(parts[:2]), node)
            for part in parts[2:]:
                out = Node("Attribute", "", node.line, node.column).put("value", out).put("attr", string(part))
            return out
        return module_name(target, node)

    def annotation(self, owner: SourceModule, node: Node) -> Node:
        if node.kind == "Constant" and node.get("value").kind == "str":
            parsed = parse_expression(node.get("value").text).get("body")
            module_offset(parsed, node.line - 1)
            return self.rewrite(owner, parsed, [])
        return self.rewrite(owner, node, [])

    def function(self, owner: SourceModule, node: Node, outer: list[str], top: bool) -> Node:
        out = module_copy(node)
        if top:
            module_set(out, "name", string(owner.bindings[node.get("name").text]))
        args = out.get("args")
        bound = outer.copy()
        for param in args.get("posonlyargs").items + args.get("args").items + args.get("kwonlyargs").items:
            bound.append(param.get("arg").text)
            module_set(param, "annotation", self.annotation(owner, param.get("annotation")))
        for key in ("defaults", "kw_defaults"):
            module_set(args, key, self.rewrite(owner, args.get(key), outer))
        if "returns" in out.keys:
            module_set(out, "returns", self.annotation(owner, node.get("returns")))
        if "decorator_list" in out.keys:
            module_set(out, "decorator_list", self.rewrite(owner, node.get("decorator_list"), []))
        walked: list[Node] = []
        scope_walk(node.get("body"), walked)
        for item in walked:
            if item.kind in ("Assign", "Delete"):
                for target in item.get("targets").items: scope_target(target, bound)
            elif item.kind in ("AnnAssign", "AugAssign", "For"):
                scope_target(item.get("target"), bound)
            elif item.kind == "withitem":
                scope_target(item.get("optional_vars"), bound)
            elif item.kind == "ExceptHandler" and item.get("name").kind != "none":
                bound.append(item.get("name").text)
            elif item.kind in ("FunctionDef", "AsyncFunctionDef", "ClassDef"):
                bound.append(item.get("name").text)
        module_set(out, "body", self.rewrite(owner, node.get("body"), bound))
        return out

    def store(self, owner: SourceModule, node: Node, locals_: list[str]) -> None:
        if node.kind == "Attribute" and self.resolve(owner, node.get("value"), locals_).startswith("@module:"):
            self.error("module attributes cannot be assigned or deleted", node)
        if node.kind in ("Tuple", "List"):
            for item in node.get("elts").items:
                self.store(owner, item, locals_)

    def comprehension_part(self, owner: SourceModule, node: Node, names: list[str], init: bool) -> Node:
        return self.init_locals(owner, node, names) if init else self.rewrite(owner, node, names)

    def comprehension(self, owner: SourceModule, node: Node, names: list[str], init: bool) -> Node:
        targets: list[str] = []
        for gen in node.get("generators").items:
            scope_target(gen.get("target"), targets)
        bound = names.copy()
        if init:
            bound = []
            for name in names:
                if name not in targets: bound.append(name)
        else:
            bound.extend(targets)
        out = module_copy(node)
        generators = out.get("generators").items
        for index in range(len(generators)):
            gen = generators[index]
            self.store(owner, gen.get("target"), bound)
            module_set(gen, "target", self.comprehension_part(owner, gen.get("target"), bound, init))
            module_set(gen, "iter", self.comprehension_part(owner, gen.get("iter"), names if index == 0 else bound, init))
            module_set(gen, "ifs", self.comprehension_part(owner, gen.get("ifs"), bound, init))
        for key in (["elt"] if node.kind == "ListComp" else ["key", "value"]):
            module_set(out, key, self.comprehension_part(owner, out.get(key), bound, init))
        return out

    def init_locals(self, owner: SourceModule, node: Node, names: list[str]) -> Node:
        if node.kind in ("ListComp", "DictComp"):
            return self.comprehension(owner, node, names, True)
        if node.kind == "Lambda":
            shadowed: list[str] = []
            args = node.get("args")
            for param in args.get("posonlyargs").items + args.get("args").items + args.get("kwonlyargs").items:
                shadowed.append(param.get("arg").text)
            free: list[str] = []
            for name in names:
                if name not in shadowed: free.append(name)
            module_set(node, "body", self.init_locals(owner, node.get("body"), free))
            return node
        if node.kind == "Name" and node.get("id").text in names:
            return module_name("@init:" + owner.name.replace(".", "~") + ":" + node.get("id").text, node)
        for i in range(len(node.items)):
            node.items[i] = self.init_locals(owner, node.items[i], names)
        if node.kind == "ExceptHandler" and node.get("name").text in names:
            module_set(node, "name", string("@init:" + owner.name.replace(".", "~") + ":" + node.get("name").text))
        return node

    def rewrite(self, owner: SourceModule, node: Node, locals_: list[str]) -> Node:
        if node.kind in ("ListComp", "DictComp"):
            return self.comprehension(owner, node, locals_, False)
        if node.kind in ("Import", "ImportFrom"):
            self.error("imports must be at module scope", node)
        if node.kind in ("Assign", "Delete"):
            for target in node.get("targets").items: self.store(owner, target, locals_)
        elif node.kind in ("AnnAssign", "AugAssign", "For"):
            self.store(owner, node.get("target"), locals_)
        if node.kind in ("Name", "Attribute"):
            binding = self.resolve(owner, node, locals_)
            if binding != "":
                return self.value(binding, node)
            if node.kind == "Name" and node.get("id").text not in locals_:
                name = node.get("id").text
                if name == "__name__":
                    value = "__main__" if owner.name == self.entry and not self.library else owner.name
                    return Node("Constant", "", node.line, node.column).put("value", string(value)).put("kind", missing())
                if name in ("i32", "i64", "f32", "f64", "HostObject", "Final", "Optional", "final", "host", "Callable", "Iterator", "Generator", "Coroutine", "TracebackType", "run", "checkpoint", "wait_all", "gather", "read_bytes", "write_bytes"):
                    self.error("'" + name + "' must be imported in this module", node)
        if node.kind in ("FunctionDef", "AsyncFunctionDef", "Lambda"):
            return self.function(owner, node, locals_, False)
        if node.kind == "ClassDef":
            self.error("nested classes are not supported", node)
        out = Node(node.kind, node.text, node.line, node.column)
        out.keys = node.keys.copy()
        for i in range(len(node.items)):
            key = node.keys[i] if len(node.keys) > i else ""
            if key == "annotation":
                out.items.append(self.annotation(owner, node.items[i]))
            else:
                out.items.append(self.rewrite(owner, node.items[i], locals_))
        return out

    def link(self) -> Node:
        entry = self.modules[self.entry]
        local_imports = False
        for node in entry.tree.get("body").items:
            if node.kind == "Import":
                for alias in node.get("names").items:
                    if not module_builtin(alias.get("name").text): local_imports = True
            elif node.kind == "ImportFrom":
                if node.get("level").text != "0" or not module_builtin(node.get("module").text): local_imports = True
        # Preserve the existing single-source pipeline and its exact artifacts.
        if not self.library and not local_imports and "." not in self.entry:
            return entry.tree
        self.load(self.entry, Node("Module", "", 1, 0))
        body: list[Node] = []
        for statement in self.statements:
            owner = self.modules[statement.owner]
            node = statement.node
            if node.kind in ("FunctionDef", "AsyncFunctionDef"):
                body.append(self.function(owner, node, [], True))
            elif node.kind == "ClassDef":
                out = module_copy(node)
                module_set(out, "name", string(owner.bindings[node.get("name").text]))
                module_set(out, "bases", self.rewrite(owner, node.get("bases"), []))
                module_set(out, "decorator_list", self.rewrite(owner, node.get("decorator_list"), []))
                members: list[Node] = []
                for member in node.get("body").items:
                    if member.kind == "AnnAssign":
                        field = module_copy(member)
                        module_set(field, "annotation", self.annotation(owner, member.get("annotation")))
                        module_set(field, "value", self.rewrite(owner, member.get("value"), []))
                        members.append(field)
                    else:
                        members.append(self.rewrite(owner, member, []))
                module_set(out, "body", sequence(members))
                body.append(out)
            elif node.kind in ("Assign", "AnnAssign", "AugAssign"):
                self.error("module-level variables are not supported; export functions or classes", node)
            elif not docstring(node):
                walked: list[Node] = []
                scope_walk(owner.tree, walked)
                names: list[str] = []
                for item in walked:
                    if item.kind in ("FunctionDef", "AsyncFunctionDef", "ClassDef") and item not in owner.tree.get("body").items:
                        self.error("module functions and classes must be declared directly at module scope", item)
                    if item.kind in ("Assign", "Delete"):
                        for target in item.get("targets").items: scope_target(target, names)
                    elif item.kind in ("AnnAssign", "AugAssign", "For"):
                        scope_target(item.get("target"), names)
                    elif item.kind == "withitem":
                        scope_target(item.get("optional_vars"), names)
                    elif item.kind == "ExceptHandler" and item.get("name").kind != "none":
                        names.append(item.get("name").text)
                for name in names:
                    if name in owner.bindings:
                        self.error("cannot rebind module declaration or import '" + name + "'", node)
                body.append(self.init_locals(owner, self.rewrite(owner, node, names), names))
        return Node("Module").put("body", sequence(list(self.imports.values()) + body)).put("type_ignores", sequence([]))


class ModuleEnvelope:
    source: str
    pos: i32

    def __init__(self, source: str) -> None:
        self.source = source
        self.pos = 0

    def line(self) -> str:
        end = self.source.find("\n", self.pos)
        if end < 0:
            raise ValueError("invalid project envelope")
        value = self.source[self.pos:end]
        self.pos = end + 1
        return value

    def read(self) -> ModuleProject:
        if self.line() != "SNEK-PROJECT-1":
            raise ValueError("invalid project envelope version")
        project = ModuleProject(self.line())
        count = i32(self.line())
        offset = 0
        for unused in range(count):
            name = self.line()
            path = self.line()
            package = self.line() == "1"
            size = i32(self.line())
            source = ""
            if size >= 0:
                end = self.pos + size
                if end >= len(self.source) or self.source[end] != "\n":
                    raise ValueError("invalid project source length")
                source = self.source[self.pos:end]
                self.pos = end + 1
            if name in project.modules:
                raise ValueError("duplicate project module: " + name)
            try:
                project.modules[name] = SourceModule(name, path, package, size >= 0, source, offset)
            except ValueError as error:
                message = str(error)
                comma = message.find(", column ")
                colon = message.find(": ", comma)
                if message.startswith("line ") and comma >= 0 and colon >= 0:
                    fail("syntax error: " + message[colon + 2:], offset + i32(message[5:comma]), i32(message[comma + 9:colon]) + 1)
                raise ValueError(path + ": " + message)
            offset += source.count("\n") + 2
        if self.pos != len(self.source):
            raise ValueError("trailing project envelope data")
        return project


def module_project(source: str, library: bool = False) -> Node:
    project = ModuleEnvelope(source).read()
    project.library = library
    return project.link()


def module_library(source: str) -> Node:
    project = ModuleProject("library")
    project.library = True
    project.modules["library"] = SourceModule("library", "<input>", False, True, source, 0)
    return project.link()
