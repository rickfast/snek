"""Lexical closure analysis over the owned syntax tree, written in Snek."""
from __future__ import annotations
from bootstrap.parser import Node, missing, sequence
from bootstrap.declarations import declaration_error


class BodyScope:
    node: Node
    bound: list[str]
    free: list[str]
    cells: list[str]
    nonlocal_names: list[str]
    children: list[BodyScope]

    def __init__(self, node: Node) -> None:
        self.node = node
        self.bound = []
        self.free = []
        self.cells = []
        self.nonlocal_names = []
        self.children = []

    def child(self, node: Node) -> BodyScope:
        for child in self.children:
            if child.node is node:
                return child
        raise ValueError("missing lexical scope")


def comprehension_loop(node: Node, first_iter: Node) -> Node:
    value = Node("List", "", node.line, node.column).put("elts", sequence([node.get("elt")])) if node.kind == "ListComp" else Node("Dict", "", node.line, node.column).put("keys", sequence([node.get("key")])).put("values", sequence([node.get("value")]))
    body = sequence([Node("ComprehensionEmit", "", node.line, node.column).put("value", value)])
    generators = node.get("generators").items
    for index in range(len(generators) - 1, -1, -1):
        gen = generators[index]
        filters = gen.get("ifs").items
        for j in range(len(filters) - 1, -1, -1):
            cond = filters[j]
            body = sequence([Node("If", "", cond.line, cond.column).put("test", cond).put("body", body).put("orelse", sequence([]))])
        iterable = gen.get("iter") if index > 0 else first_iter
        body = sequence([Node("For", "", gen.line, gen.column).put("target", gen.get("target"))
                         .put("iter", iterable).put("body", body).put("orelse", sequence([]))])
    return body.items[0]


def scope_body(node: Node) -> Node:
    if node.kind in ("ListComp", "DictComp"):
        return sequence([comprehension_loop(node, missing())])
    return node.get("body")


def scope_walk(node: Node, out: list[Node]) -> None:
    out.append(node)
    if node.kind in ("ListComp", "DictComp"):
        scope_walk(node.get("generators").items[0].get("iter"), out)
    elif node.kind == "AnnAssign":
        scope_walk(node.get("target"), out)
        scope_walk(node.get("value"), out)
    elif node.kind not in ("FunctionDef", "AsyncFunctionDef", "Lambda", "ClassDef"):
        for child in node.items:
            scope_walk(child, out)


def add_name(names: list[str], name: str) -> None:
    if name not in names:
        names.append(name)


def scope_target(node: Node, names: list[str]) -> None:
    if node.kind == "Name":
        add_name(names, node.get("id").text)
    elif node.kind in ("Tuple", "List"):
        for item in node.get("elts").items:
            scope_target(item, names)


def body_scope(node: Node, outer: list[str]) -> BodyScope:
    scope = BodyScope(node)
    nodes: list[Node] = []
    scope_walk(scope_body(node), nodes)
    arguments = node.get("args") if "args" in node.keys else missing()
    params: list[str] = []
    if arguments.kind != "none":
        for param in arguments.get("posonlyargs").items + arguments.get("args").items + arguments.get("kwonlyargs").items:
            name = param.get("arg").text
            if name in params:
                declaration_error("duplicate parameter name", param)
            params.append(name)
    scope.bound = params.copy()
    used: list[str] = []
    for item in nodes:
        if item.kind == "Global":
            declaration_error("global is not supported; keep state in objects or lexical closures", item)
        if item.kind == "Nonlocal":
            for value in item.get("names").items:
                name = value.text
                if name not in outer:
                    declaration_error("no binding for nonlocal '" + name + "' found", item)
                if name in params:
                    declaration_error("parameter '" + name + "' cannot be nonlocal", item)
                if name in used or name in scope.bound:
                    declaration_error("nonlocal '" + name + "' must precede its use or assignment", item)
                add_name(scope.nonlocal_names, name)
        elif item.kind == "Name":
            add_name(used, item.get("id").text)
        elif item.kind in ("Assign", "Delete"):
            for target in item.get("targets").items:
                scope_target(target, scope.bound)
        elif item.kind in ("AnnAssign", "AugAssign", "For"):
            scope_target(item.get("target"), scope.bound)
        elif item.kind == "withitem":
            scope_target(item.get("optional_vars"), scope.bound)
        elif item.kind == "ExceptHandler" and item.get("name").kind != "none":
            add_name(scope.bound, item.get("name").text)
        elif item.kind in ("FunctionDef", "AsyncFunctionDef", "ClassDef"):
            add_name(scope.bound, item.get("name").text)
    for name in scope.nonlocal_names:
        if name in scope.bound:
            scope.bound.remove(name)
    for name in used + scope.nonlocal_names:
        if (name not in scope.bound and name in outer) or name in scope.nonlocal_names:
            add_name(scope.free, name)
    for item in nodes:
        if item.kind in ("FunctionDef", "AsyncFunctionDef", "Lambda", "ListComp", "DictComp"):
            child = body_scope(item, outer + scope.bound)
            scope.children.append(child)
            for name in child.free:
                add_name(scope.cells if name in scope.bound else scope.free, name)
    return scope
