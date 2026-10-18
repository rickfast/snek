"""Typed library ABI and host bindings, shared by both emitters."""
from __future__ import annotations
from snek import i32
from bootstrap.parser import Node, string, json_string
from bootstrap.machine_emit import machine_fields_wat
from bootstrap.emit_data import emit_library_runtime, emit_library_javascript


class LibraryExport:
    name: str
    target: str
    params: list[str]
    ret: str
    heap: str

    def __init__(self, name: str, target: str, params: list[str], ret: str, heap: str = "") -> None:
        self.name = name
        self.target = target
        self.params = params
        self.ret = ret
        self.heap = heap


class LibraryClass:
    name: str
    base: str

    def __init__(self, name: str, base: str) -> None:
        self.name = name
        self.base = base


def library_class_name(ty: str) -> str:
    if not ty.startswith("class:"): return ""
    return ty[6:-7] if ty.endswith(" | None") else ty[6:]


def library_class_label(name: str) -> str:
    return name[6:].replace("~", ".").replace(":", ".") if name.startswith("@user:") else name


def library_boundary_types(exports: list[LibraryExport], hosts: list[LibraryExport]) -> list[str]:
    """Close boundary metadata over the types nested in lists and callbacks."""
    types: list[str] = []
    for item in exports + hosts:
        for ty in item.params + [item.ret]:
            if ty not in types: types.append(ty)
    i = 0
    while i < len(types):
        ty = types[i]
        i += 1
        children: list[str] = []
        if ty.startswith("list["): children.append(library_list_element(ty))
        elif ty.startswith("Callable["): children = library_callback_types(ty)
        for child in children:
            child = library_callback_result(child)
            if child not in types: types.append(child)
    return types


def library_class_types(exports: list[LibraryExport], hosts: list[LibraryExport]) -> list[str]:
    types: list[str] = []
    for ty in library_boundary_types(exports, hosts):
        if ty.startswith("class:"): types.append(ty)
    return types


def library_class_code(types: list[str]) -> str:
    lines: list[str] = []
    for i in range(len(types)):
        name = library_class_name(types[i])
        lines.append('(func (export "__snek_class_test_' + str(i) + '") (param $value (ref any)) (result i32) (ref.test (ref $' + name + ') (local.get $value)))')
    return "\n".join(lines)


def library_class_declarations(types: list[str], classes: list[LibraryClass]) -> str:
    bases: dict[str, str] = {}
    for cls in classes: bases[cls.name] = cls.base
    names: list[str] = []
    for ty in types:
        name = library_class_name(ty)
        while name != "" and name not in names:
            if name not in bases: raise ValueError("unknown library class: " + name)
            names.append(name)
            name = bases[name]
    lines: list[str] = []
    for name in names:
        parents: list[str] = []
        base = bases[name]
        while base != "":
            parents.append(json_string(library_class_label(base)))
            base = bases[base]
        lines.append("  " + json_string(library_class_label(name)) + ": " + (" | ".join(parents) if len(parents) > 0 else "never") + ";")
    return ("declare const snekClassType: unique symbol;\nexport interface SnekClassParents {\n" + "\n".join(lines) + "\n}\n"
            + "interface SnekHandle<T> { readonly [snekClassType]: (type: T) => void; }\n"
            + "export type SnekObject<T extends keyof SnekClassParents> = SnekHandle<T | SnekClassParents[T]>;\n")


def library_target(name: str, entry: str) -> str:
    if len(name) == 0 or not (name[0].isalpha() or name[0] == "_"):
        raise ValueError("library export must be a function name: " + name)
    for char in name:
        if not (char.isascii() and (char.isalnum() or char == "_")):
            raise ValueError("library export names must be ASCII identifiers: " + name)
    if name.startswith("__snek_") or name in ("memory", "then"):
        raise ValueError("reserved library export name: " + name)
    return "@user:" + entry.replace(".", "~") + ":" + name


def library_callback_types(ty: str) -> list[str]:
    """Decode the checker's canonical display form, validating nested boundaries."""
    if not ty.startswith("Callable[[") or not ty.endswith("]"):
        raise ValueError("invalid callback signature: " + ty)
    end = -1
    depth = 0
    start = 10
    types: list[str] = []
    for i in range(10, len(ty)):
        char = ty[i]
        if char == "[": depth += 1
        elif char == "]":
            if depth == 0:
                end = i
                if i > start: types.append(ty[start:i])
                break
            depth -= 1
        elif char == "," and depth == 0:
            types.append(ty[start:i])
            start = i + 2
    if end < 0 or ty[end:end + 3] != "], ":
        raise ValueError("invalid callback signature: " + ty)
    types.append(ty[end + 3:-1])
    for i in range(len(types)):
        value = library_callback_result(types[i]) if i == len(types) - 1 else types[i]
        if "[" in value and not (value.startswith("list[") or value.startswith("Callable[")):
            raise ValueError("callback signatures cannot contain unsupported collections or bare coroutines: " + ty)
        if value != "None" or i != len(types) - 1: library_type(value)
    return types


def library_callback_result(ty: str) -> str:
    return ty[22:-1] if ty.startswith("Coroutine[None, None, ") and ty.endswith("]") else ty


def library_callback_async(callbacks: list[str]) -> bool:
    for ty in callbacks:
        if library_callback_types(ty)[-1].startswith("Coroutine["): return True
    return False


def library_callback_heap(ty: str) -> str:
    types = library_callback_types(ty)
    parts: list[str] = []
    for value in types:
        inner = library_callback_result(value)
        part = library_mangle(inner)
        parts.append("coro<" + part + ">" if value.startswith("Coroutine[") else part)
    return "$callable<" + "~".join(parts[:-1]) + ":" + parts[-1] + ">"


def library_type(ty: str) -> str:
    if ty.startswith("class:"): return "(ref " + ("null " if ty.endswith(" | None") else "") + "$" + library_class_name(ty) + ")"
    if ty.startswith("list["): return "(ref " + library_list_heap(ty) + ")"
    if ty.startswith("Callable["): return "(ref " + library_callback_heap(ty) + ")"
    if ty == "HostObject": return "(ref extern)"
    if ty == "HostObject | None": return "(ref null extern)"
    if ty == "str": return "i32"
    if ty == "bool": return "i32"
    if ty in ("i32", "i64", "f32", "f64"): return ty
    raise ValueError("library boundaries support only i32, i64, f32, f64, bool, str, object handles, supported list/Callable, and None returns; got " + ty)


def library_list_element(ty: str) -> str:
    elem = ty[5:-1]
    if not ty.startswith("list[") or not ty.endswith("]"):
        raise ValueError("invalid library list type: " + ty)
    if elem.startswith("list["):
        library_list_element(elem)
    elif elem.startswith("Callable["):
        library_callback_types(elem)
    elif elem.startswith("class:"):
        if library_class_name(elem) == "": raise ValueError("invalid library class element: " + ty)
    elif elem not in ("i32", "i64", "f32", "f64", "bool", "str", "HostObject", "HostObject | None"):
        raise ValueError("library list elements must be scalar, string, object handles, supported callbacks or lists; got " + ty)
    return elem


def library_mangle(ty: str) -> str:
    if ty.startswith("class:"): return library_class_name(ty) + ("?" if ty.endswith(" | None") else "")
    if ty.startswith("list["): return "list<" + library_mangle(library_list_element(ty)) + ">"
    if ty.startswith("Callable["): return library_callback_heap(ty)[1:]
    return "none" if ty == "None" else ty.replace(" | None", "?")


def library_list_heap(ty: str) -> str:
    return "$list." + library_mangle(library_list_element(ty))


def library_lists(exports: list[LibraryExport], hosts: list[LibraryExport]) -> list[str]:
    lists: list[str] = []
    for ty in library_boundary_types(exports, hosts):
        if ty.startswith("list["): lists.append(ty)
    return lists


def library_list_code(lists: list[str]) -> str:
    lines: list[str] = []
    for i in range(len(lists)):
        ty = lists[i]
        elem = library_list_element(ty)
        heap = library_list_heap(ty)
        param = " (param $list (ref " + heap + "))"
        lines.append('(func (export "__snek_list_len_' + str(i) + '")' + param + ' (result i32) (struct.get ' + heap + ' $len (local.get $list)))')
        for op in ("get", "set", "append", "pop"):
            head = '(func (export "__snek_list_' + op + '_' + str(i) + '")' + param
            args = "(local.get $list)"
            if op in ("get", "set"):
                head += " (param $index i32)"
                args += " (local.get $index)"
            if op in ("set", "append"):
                head += " (param $value " + library_type(elem) + ")"
                value = "(local.get $value)"
                if elem == "str":
                    head += " (param $length i32)"
                    value = "(call $__snek_read " + value + " (local.get $length))"
                args += " " + value
            else: head += " (result " + library_type(elem) + ")"
            body = "(call " + heap + "." + op + " " + args + ")"
            if elem == "str" and op in ("get", "pop"): body = "(call $__snek_write " + body + ")"
            lines.append(head + " " + body + ")")
    return "\n".join(lines)


def library_ts(ty: str) -> str:
    if ty.startswith("class:"): return "SnekObject<" + json_string(library_class_label(library_class_name(ty))) + ">" + (" | null" if ty.endswith(" | None") else "")
    if ty.startswith("list["): return "SnekList<" + library_ts(library_list_element(ty)) + ">"
    if ty.startswith("Coroutine["): return "PromiseLike<" + library_ts(library_callback_result(ty)) + ">"
    if ty.startswith("Callable["):
        types = library_callback_types(ty)
        params: list[str] = []
        for i in range(len(types) - 1): params.append("arg" + str(i) + ": " + library_ts(types[i]))
        return "((" + ", ".join(params) + ") => " + ("undefined" if types[-1] == "None" else library_ts(types[-1])) + ")"
    if ty == "HostObject": return "object"
    if ty == "HostObject | None": return "object | null"
    if ty == "None": return "void"
    if ty == "str": return "string"
    if ty == "bool": return "boolean"
    if ty == "i64": return "bigint"
    return "number"


def library_nullable(ty: str) -> str:
    if ty.startswith("class:"): return "(ref null $" + library_class_name(ty) + ")"
    if ty.startswith("list["): return "(ref null " + library_list_heap(ty) + ")"
    if ty == "HostObject": return "(ref null extern)"
    if ty.startswith("Callable["): return "(ref null " + library_callback_heap(ty) + ")"
    return library_type(ty)


def library_callbacks(exports: list[LibraryExport], hosts: list[LibraryExport]) -> list[str]:
    callbacks: list[str] = []
    for ty in library_boundary_types(exports, hosts):
        if ty.startswith("Callable["): callbacks.append(ty)
    return callbacks


def library_callback_imports(callbacks: list[str], offset: i32) -> str:
    lines: list[str] = []
    for i in range(len(callbacks)):
        types = library_callback_types(callbacks[i])
        params = " (param (ref extern))"
        for ty in types[:-1]: params += " (param " + ("i32 i32" if ty == "str" else library_type(ty)) + ")"
        ret = library_callback_result(types[-1])
        result = "" if ret == "None" else " (result " + ("i32 i32" if ret == "str" else library_type(ret)) + ")"
        if types[-1].startswith("Coroutine["):
            lines.append('(import "snek_callback_async" "' + str(i) + '" (func $__snek_host_' + str(offset + i) + params + ' (result (ref extern))))')
            lines.append('(import "snek_callback_result" "' + str(i) + '" (func $__snek_result_' + str(offset + i) + ' (param (ref extern))' + result + '))')
        else:
            lines.append('(import "snek_callback" "' + str(i) + '" (func $__snek_host_' + str(offset + i) + params + result + '))')
    return "\n".join(lines)


def library_sync_export(item: LibraryExport, head: str, args: list[str]) -> str:
    result = ""
    if item.ret != "None":
        result = library_type(item.ret)
        head += " (result " + result + ") (local $result " + library_nullable(item.ret) + ")"
    call = "(call $" + item.target + " " + " ".join(args) + ")"
    if item.ret == "str": call = "(call $__snek_write " + call + ")"
    if result != "": call = "(local.set $result " + call + ")"
    value = "(local.get $result)" if result != "" else ""
    if item.ret == "HostObject" or item.ret.startswith("Callable[") or item.ret.startswith("list["): value = "(ref.as_non_null " + value + ")"
    if item.ret.startswith("class:") and not item.ret.endswith(" | None"): value = "(ref.as_non_null " + value + ")"
    return head + " (call $rt.uncaught (block $failed (result (ref $BaseException)) (try_table (catch $snek.exc $failed) " + call + ") (call $rt.flush) (return " + value + "))) (unreachable))"


def library_callback_code(callbacks: list[str], offset: i32) -> str:
    lines: list[str] = []
    for i in range(len(callbacks)):
        ty = callbacks[i]
        types = library_callback_types(ty)
        params = types[:-1]
        ret = library_callback_result(types[-1])
        async_heap = ""
        if types[-1].startswith("Coroutine["):
            async_heap = "$coro<" + library_mangle(ret) + ">"
        heap = library_callback_heap(ty)
        prefix = "$__snek_callback_" + str(i)
        env = prefix + ".env"
        lines.append("(type " + env + " (sub final $env<> (struct (field (ref extern)))))")
        lines.append("(elem declare func " + prefix + ".step)")
        lines.append('(func (export "__snek_wrap_' + str(i) + '") (param $fn (ref extern)) (result (ref ' + heap + ')) (struct.new ' + heap + ' (ref.func ' + prefix + '.step) (struct.new ' + env + ' (local.get $fn))))')
        typed = ""
        values = ""
        head = '(func (export "__snek_call_' + str(i) + '") (param $closure (ref ' + heap + '))'
        args = ["(local.get $closure)"]
        for p in range(len(params)):
            name = "$p" + str(p)
            value_type = "(ref $str)" if params[p] == "str" else library_type(params[p])
            typed += " (param " + name + " " + value_type + ")"
            values += " (local.get " + name + ")"
            head += " (param " + name + " " + library_type(params[p]) + ")"
            value = "(local.get " + name + ")"
            if params[p] == "str":
                head += " (param " + name + "n i32)"
                value = "(call $__snek_read " + value + " (local.get " + name + "n))"
            elif params[p] == "bool": value = "(i32.ne " + value + " (i32.const 0))"
            args.append(value)
        result = "" if ret == "None" else " (result " + ("(ref $str)" if ret == "str" else library_type(ret)) + ")"
        if async_heap != "": result = " (result (ref " + async_heap + "))"
        lines.append("(func " + prefix + ".step (type " + heap + ".sig) (param $env (ref $env<>))" + typed + result + " (call " + prefix + ".host (struct.get " + env + " 0 (ref.cast (ref " + env + ") (local.get $env)))" + values + "))")
        lines.append(library_host_wrapper(LibraryExport("callback " + str(i), prefix[1:] + ".host", ["HostObject"] + params, ret, async_heap), offset + i))
        lines.append("(func " + prefix + ".invoke (param $closure (ref " + heap + "))" + typed + result + " (call_ref " + heap + ".sig (struct.get " + heap + " $env (local.get $closure))" + values + " (struct.get " + heap + " $fn (local.get $closure))))")
        item = LibraryExport("__snek_callback_" + str(i), prefix[1:] + ".invoke", [], ret, async_heap)
        lines.append(library_async_export(item, head, args) if async_heap != "" else library_sync_export(item, head, args))
    return "\n".join(lines)


def library_host_imports(hosts: list[LibraryExport], objects: bool = False, async_callbacks: bool = False) -> str:
    lines: list[str] = []
    if objects:
        lines.append('(import "snek_object" "same" (func $__snek_object_same (param (ref null extern) (ref null extern)) (result i32)))')
    seen: dict[str, str] = {}
    for index in range(len(hosts)):
        item = hosts[index]
        signature = ",".join(item.params) + "->" + item.ret + (" async" if item.heap != "" else "")
        if item.name in seen and seen[item.name] != signature:
            raise ValueError("conflicting signatures for host import: " + item.name)
        seen[item.name] = signature
        params = ""
        for ty in item.params:
            params += " (param " + ("i32 i32" if ty == "str" else library_type(ty)) + ")"
        result = "" if item.ret == "None" else " (result " + ("i32 i32" if item.ret == "str" else library_type(item.ret)) + ")"
        if item.heap != "":
            lines.append('(import "snek_host_async" ' + json_string(item.name) + ' (func $__snek_host_' + str(index) + params + ' (result (ref extern))))')
            lines.append('(import "snek_host_result" ' + json_string(item.name) + ' (func $__snek_result_' + str(index) + ' (param (ref extern))' + result + '))')
        else:
            lines.append('(import "snek_host" ' + json_string(item.name) + ' (func $__snek_host_' + str(index) + params + result + '))')
    for item in hosts:
        if item.heap != "":
            async_callbacks = True
            break
    if async_callbacks:
        lines.append('(import "snek_async" "status" (func $__snek_async_status (param (ref extern)) (result i32)))')
        lines.append('(import "snek_async" "error" (func $__snek_async_error (param (ref extern)) (result i32 i32)))')
        lines.append('(import "snek_async" "drop" (func $__snek_async_drop (param (ref extern))))')
    return "\n".join(lines)


def library_host_wrapper(item: LibraryExport, index: i32) -> str:
    if item.heap != "": return library_async_host(item, index)
    head = "(func $" + item.target
    args: list[str] = []
    locals: list[str] = ["(local $mark i32)"]
    body: list[str] = ["(local.set $mark (global.get $__snek_bump))"]
    for i in range(len(item.params)):
        ty = item.params[i]
        p = "$p" + str(i)
        head += " (param " + p + " " + ("(ref $str)" if ty == "str" else library_type(ty)) + ")"
        if ty == "str":
            locals.append("(local " + p + "ptr i32) (local " + p + "len i32)")
            body.append("(local.set " + p + "ptr (call $__snek_write (local.get " + p + "))) (local.set " + p + "len (global.get $__snek_length))")
            args.append("(local.get " + p + "ptr) (local.get " + p + "len)")
        else:
            args.append("(local.get " + p + ")")
    result = ""
    if item.ret != "None":
        result = "(ref $str)" if item.ret == "str" else library_type(item.ret)
        head += " (result " + result + ")"
        locals.append("(local $result " + result + ")")
    call = "(call $__snek_host_" + str(index) + " " + " ".join(args) + ")"
    if item.ret == "str":
        locals.append("(local $ptr i32) (local $len i32)")
        body.append(call + " (local.set $len) (local.set $ptr)")
        body.append("(local.set $result (call $__snek_read (local.get $ptr) (local.get $len)))")
    else:
        body.append("(local.set $result " + call + ")" if result != "" else call)
    # Returned strings are copied to the GC heap before this scratch frame ends.
    # An escaping JS exception is cleaned up by the outer JS enter/finally.
    body.append("(global.set $__snek_bump (local.get $mark))")
    if result != "": body.append("(local.get $result)")
    return head + " " + " ".join(locals) + " " + " ".join(body) + ")"


def library_async_host(item: LibraryExport, index: i32) -> str:
    B = item.heap
    F = "$__snek_host_frame_" + str(index)
    result_type = "(ref null $str)" if item.ret == "str" else library_type(item.ret) if item.ret != "None" else ""
    if item.ret == "HostObject" or item.ret.startswith("Callable[") or item.ret.startswith("list["): result_type = library_nullable(item.ret)
    if item.ret.startswith("class:"): result_type = library_nullable(item.ret)
    elem = Node("Type").put("none", Node("scalar", "true" if item.ret == "None" else "false")).put("storage", string(result_type))
    fields = machine_fields_wat(Node("Machine").put("heap", string(B)).put("elem", elem))
    params = ""
    values = ""
    extra = ""
    locals = ""
    start = "(local.set $mark (global.get $__snek_bump))"
    args = ""
    for i in range(len(item.params)):
        p = "$p" + str(i)
        ty = item.params[i]
        value_type = "(ref $str)" if ty == "str" else library_type(ty)
        params += " (param " + p + " " + value_type + ")"
        values += " (local.get " + p + ")"
        extra += " (field " + p + " " + value_type + ")"
        value = "(struct.get " + F + " " + p + " (ref.cast (ref " + F + ") (local.get $a)))"
        if ty == "str":
            locals += " (local " + p + "ptr i32) (local " + p + "len i32)"
            start += " (local.set " + p + "ptr (call $__snek_write " + value + ")) (local.set " + p + "len (global.get $__snek_length))"
            args += " (local.get " + p + "ptr) (local.get " + p + "len)"
        else: args += " " + value
    frame = "(ref.cast (ref " + F + ") (local.get $a))"
    request = "(struct.get " + F + " $request " + frame + ")"
    clear = "(struct.set " + F + " $request " + frame + " (ref.null extern))"
    start += " (struct.set " + F + " $request " + frame + " (call $__snek_host_" + str(index) + args + ")) (global.set $__snek_bump (local.get $mark))"
    start += " (struct.set " + B + " $pc (local.get $a) (i32.const 1)) (struct.set " + B + " $busy (local.get $a) (i32.const 0)) (return (i32.const 1))"
    default = "" if item.ret == "None" else " (ref.null $str)" if item.ret == "str" else " (" + library_type(item.ret) + ".const 0)"
    if item.ret in ("HostObject", "HostObject | None"): default = " (ref.null extern)"
    if item.ret.startswith("Callable["): default = " (ref.null " + library_callback_heap(item.ret) + ")"
    if item.ret.startswith("list["): default = " (ref.null " + library_list_heap(item.ret) + ")"
    if item.ret.startswith("class:"): default = " (ref.null $" + library_class_name(item.ret) + ")"
    finish = "(struct.set " + B + " $pc (local.get $a) (i32.const -1)) (struct.set " + B + " $busy (local.get $a) (i32.const 0)) " + clear
    take = "(call $__snek_result_" + str(index) + " (local.get $request))"
    if item.ret == "str":
        take += " (local.set $len) (local.set $ptr)"
        take += " (struct.set " + B + " $value (local.get $a) (call $__snek_read (local.get $ptr) (local.get $len)))"
    elif item.ret != "None":
        take = "(struct.set " + B + " $value (local.get $a) " + take + ")"
    return ("(type " + F + " (sub final " + B + " (struct " + fields + " (field $request (mut (ref null extern)))" + extra + ")))\n"
            + "(elem declare func " + F + ".step)\n"
            + "(func $" + item.target + params + " (result (ref " + B + ")) (struct.new " + F + " (i32.const 0) (i32.const 0) (i32.const -1) (i32.const 0) (i32.const 0) (ref.func " + F + ".step)" + default + " (ref.null extern)" + values + "))\n"
            + "(func " + F + ".step (type " + B + ".sig) (param $a (ref " + B + ")) (param $error (ref null $BaseException)) (result i32)"
            + " (local $mark i32) (local $ptr i32) (local $len i32) (local $status i32) (local $request (ref extern)) (local $message (ref $str))" + locals
            + " (if (i32.eqz (ref.is_null (local.get $error))) (then (if (i32.eqz (ref.is_null " + request + ")) (then (call $__snek_async_drop (ref.as_non_null " + request + ")))) " + finish + " (throw $snek.exc (ref.as_non_null (local.get $error)))))"
            + " (if (i32.eqz (struct.get " + B + " $pc (local.get $a))) (then " + start + ")) "
            + " (local.set $request (ref.as_non_null " + request + ")) (local.set $mark (global.get $__snek_bump))"
            + " (local.set $status (call $__snek_async_status (local.get $request)))"
            + " (if (i32.eqz (local.get $status)) (then (struct.set " + B + " $busy (local.get $a) (i32.const 0)) (return (i32.const 1)))) " + finish
            + " (if (i32.lt_s (local.get $status) (i32.const 0)) (then (call $__snek_async_error (local.get $request)) (local.set $len) (local.set $ptr)"
            + " (local.set $message (call $__snek_read (local.get $ptr) (local.get $len))) (global.set $__snek_bump (local.get $mark))"
            + " (throw $snek.exc (call $RuntimeError.__new__ (local.get $message))))) "
            + take + " (global.set $__snek_bump (local.get $mark)) (i32.const 0))")


def library_async_export(item: LibraryExport, head: str, args: list[str]) -> str:
    B = item.heap
    value = "(local.get $task)"
    parameter = " (param $task (ref " + B + "))"
    factory = "(local.set $task (call $" + item.target + " " + " ".join(args) + ")) (call " + B + ".claim " + value + ")"
    catch = " (call $rt.uncaught (block $failed (result (ref $BaseException)) (try_table (catch $snek.exc $failed) "
    lines: list[str] = []
    lines.append(head + " (result (ref " + B + ")) (local $task (ref " + B + "))" + catch + factory + " (return " + value + ")) (unreachable))) (unreachable))")
    inject = "(if (result (ref null $BaseException)) (i32.eq (local.get $cancel) (i32.const 1)) (then (call $GeneratorExit.__new__ (ref.null $str))) (else (ref.null $BaseException)))"
    lines.append('(func (export "__snek_step_' + item.name + '")' + parameter + ' (param $cancel i32) (result i32) (local $pending i32) (local $error (ref $BaseException))'
                 + " (local.set $error (block $failed (result (ref $BaseException)) (try_table (catch $snek.exc $failed)"
                 + " (local.set $pending (call " + B + ".resume " + value + " " + inject + ")) (call $rt.flush) (return (local.get $pending))) (unreachable)))"
                 + " (if (i32.and (i32.ne (local.get $cancel) (i32.const 0)) (ref.test (ref $GeneratorExit) (local.get $error))) (then (call $rt.flush) (return (i32.const 0))))"
                 + " (call $rt.uncaught (local.get $error)) (unreachable))")
    result = ""
    body = ""
    if item.ret != "None":
        result = " (result " + library_type(item.ret) + ")"
        body = "(struct.get " + B + " $value " + value + ")"
        if item.ret == "str": body = "(call $__snek_write (ref.as_non_null " + body + "))"
        elif item.ret == "HostObject" or item.ret.startswith("Callable[") or item.ret.startswith("list["): body = "(ref.as_non_null " + body + ")"
        elif item.ret.startswith("class:") and not item.ret.endswith(" | None"): body = "(ref.as_non_null " + body + ")"
    lines.append('(func (export "__snek_result_' + item.name + '")' + parameter + result + " " + body + ")")
    return "\n".join(lines)


def library_code(exports: list[LibraryExport], hosts: list[LibraryExport], classes: list[LibraryClass]) -> str:
    if len(exports) == 0:
        raise ValueError("--library requires at least one --export FUNCTION")
    callbacks = library_callbacks(exports, hosts)
    lists = library_lists(exports, hosts)
    objects = library_class_types(exports, hosts)
    class_declarations = library_class_declarations(objects, classes)
    lines: list[str] = [emit_library_runtime(), library_callback_code(callbacks, len(hosts)), library_list_code(lists), library_class_code(objects)]
    signatures: list[str] = []
    declarations: list[str] = []
    seen: list[str] = []
    for item in exports:
        if item.name in seen:
            raise ValueError("duplicate library export: " + item.name)
        seen.append(item.name)
        head = '(func $__snek_export_' + item.name + ' (export ' + json_string(item.name) + ')'
        args: list[str] = []
        types: list[str] = []
        ts: list[str] = []
        for i in range(len(item.params)):
            ty = item.params[i]
            p = "$p" + str(i)
            head += " (param " + p + " " + library_type(ty) + ")"
            value = "(local.get " + p + ")"
            if ty == "str":
                head += " (param " + p + "n i32)"
                value = "(call $__snek_read " + value + " (local.get " + p + "n))"
            elif ty == "bool":
                value = "(i32.ne " + value + " (i32.const 0))"
            args.append(value)
            types.append(json_string(ty))
            ts.append("arg" + str(i) + ": " + library_ts(ty))
        if item.heap != "":
            if item.ret != "None": library_type(item.ret)
            lines.append(library_async_export(item, head, args))
            signatures.append("[" + json_string(item.name) + ",[" + ",".join(types) + "]," + json_string(item.ret) + ",true]")
            ts.append("options?: SnekCallOptions")
            declarations.append("  " + json_string(item.name) + "(" + ", ".join(ts) + "): Promise<" + library_ts(item.ret) + ">;")
            continue
        lines.append(library_sync_export(item, head, args))
        signatures.append("[" + json_string(item.name) + ",[" + ",".join(types) + "]," + json_string(item.ret) + "]")
        declarations.append("  " + json_string(item.name) + "(" + ", ".join(ts) + "): " + library_ts(item.ret) + ";")
    host_signatures: list[str] = []
    host_declarations: list[str] = []
    seen_hosts: list[str] = []
    for item in hosts:
        if item.name in seen_hosts: continue
        seen_hosts.append(item.name)
        types: list[str] = []
        params: list[str] = []
        for i in range(len(item.params)):
            types.append(json_string(item.params[i]))
            params.append("arg" + str(i) + ": " + library_ts(item.params[i]))
        host_signatures.append("[" + json_string(item.name) + ",[" + ",".join(types) + "]," + json_string(item.ret) + "," + ("true" if item.heap != "" else "false") + "]")
        # `undefined`, unlike TypeScript's `void`, rejects async callbacks.
        ret = "undefined" if item.ret == "None" else library_ts(item.ret)
        if item.heap != "": ret = "PromiseLike<" + ("void" if item.ret == "None" else ret) + ">"
        host_declarations.append("  " + json_string(item.name) + "(" + ", ".join(params) + "): " + ret + ";")
    callback_signatures: list[str] = []
    for ty in callbacks:
        types = library_callback_types(ty)
        args_json: list[str] = []
        for arg in types[:-1]: args_json.append(json_string(arg))
        callback_signatures.append("[" + json_string(ty) + ",[" + ",".join(args_json) + "]," + json_string(library_callback_result(types[-1])) + "," + ("true" if types[-1].startswith("Coroutine[") else "false") + "]")
    list_signatures: list[str] = []
    for ty in lists: list_signatures.append("[" + json_string(ty) + "," + json_string(library_list_element(ty)) + "]")
    class_signatures: list[str] = []
    for ty in objects: class_signatures.append("[" + json_string(ty) + "," + json_string(library_class_label(library_class_name(ty))) + "," + ("true" if ty.endswith(" | None") else "false") + "]")
    js = "// Generated by Snek. ES module; do not edit.\nconst signatures = [" + ",".join(signatures) + "];\nconst hostSignatures = [" + ",".join(host_signatures) + "];\nconst callbackSignatures = [" + ",".join(callback_signatures) + "];\nconst listSignatures = [" + ",".join(list_signatures) + "];\nconst classSignatures = [" + ",".join(class_signatures) + "];\n" + emit_library_javascript()
    dts = "// Generated by Snek.\nexport interface SnekLibrary {\n" + "\n".join(declarations) + "\n}\n"
    dts += "export interface SnekImports {\n" + "\n".join(host_declarations) + "\n}\n"
    dts += "export interface LibraryOptions { stdout?: (text: string) => void; imports" + ("" if len(hosts) > 0 else "?") + ": SnekImports; }\n"
    dts += "export class SnekError extends Error {}\n"
    dts += class_declarations
    dts += "export interface SnekList<T> { readonly length: number; get(index: number): T; set: (index: number, value: T) => void; append: (value: T) => void; pop(): T; toArray(): T[]; }\n"
    dts += "export interface SnekCallOptions { signal?: AbortSignal; }\nexport class SnekCancelledError extends Error { constructor(reason?: unknown); }\n"
    dts += "export class SnekCompatibilityError extends Error {}\nexport class SnekLoadError extends Error {}\n"
    dts += "export function assertSupported(): void;\n"
    dts += "export function instantiate(source: BufferSource | WebAssembly.Module | Response | URL | string, options" + ("" if len(hosts) > 0 else "?") + ": LibraryOptions): Promise<SnekLibrary>;\n"
    lines.append(";; SNEK-BINDINGS " + "{\"js\":" + json_string(js) + ",\"dts\":" + json_string(dts) + "}")
    return "\n".join(lines)
