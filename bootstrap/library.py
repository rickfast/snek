"""Scalar/string library ABI and host bindings, shared by both emitters."""
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
    """Decode the checker's canonical display form; nested boundaries are explicit errors."""
    end = ty.find("], ")
    if not ty.startswith("Callable[[") or end < 0 or not ty.endswith("]"):
        raise ValueError("invalid callback signature: " + ty)
    params = ty[10:end]
    types: list[str] = params.split(", ") if params != "" else []
    types.append(ty[end + 3:-1])
    for i in range(len(types)):
        value = types[i]
        if "[" in value:
            raise ValueError("callback signatures cannot contain collections, nested callbacks, or coroutines: " + ty)
        if value != "None" or i != len(types) - 1: library_type(value)
    return types


def library_callback_heap(ty: str) -> str:
    types = library_callback_types(ty)
    parts: list[str] = []
    for value in types: parts.append("none" if value == "None" else value.replace(" | None", "?"))
    return "$callable<" + "~".join(parts[:-1]) + ":" + parts[-1] + ">"


def library_type(ty: str) -> str:
    if ty.startswith("Callable["): return "(ref " + library_callback_heap(ty) + ")"
    if ty == "HostObject": return "(ref extern)"
    if ty == "HostObject | None": return "(ref null extern)"
    if ty == "str": return "i32"
    if ty == "bool": return "i32"
    if ty in ("i32", "i64", "f32", "f64"): return ty
    raise ValueError("library boundaries support only i32, i64, f32, f64, bool, str, HostObject, HostObject | None, supported Callable, and None returns; got " + ty)


def library_ts(ty: str) -> str:
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
    if ty == "HostObject": return "(ref null extern)"
    if ty.startswith("Callable["): return "(ref null " + library_callback_heap(ty) + ")"
    return library_type(ty)


def library_callbacks(exports: list[LibraryExport], hosts: list[LibraryExport]) -> list[str]:
    callbacks: list[str] = []
    for item in exports + hosts:
        for ty in item.params + [item.ret]:
            if ty.startswith("Callable[") and ty not in callbacks:
                library_callback_types(ty)
                callbacks.append(ty)
    return callbacks


def library_callback_imports(callbacks: list[str], offset: i32) -> str:
    lines: list[str] = []
    for i in range(len(callbacks)):
        types = library_callback_types(callbacks[i])
        params = " (param (ref extern))"
        for ty in types[:-1]: params += " (param " + ("i32 i32" if ty == "str" else library_type(ty)) + ")"
        ret = types[-1]
        result = "" if ret == "None" else " (result " + ("i32 i32" if ret == "str" else library_type(ret)) + ")"
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
    if item.ret == "HostObject" or item.ret.startswith("Callable["): value = "(ref.as_non_null " + value + ")"
    return head + " (call $rt.uncaught (block $failed (result (ref $BaseException)) (try_table (catch $snek.exc $failed) " + call + ") (call $rt.flush) (return " + value + "))) (unreachable))"


def library_callback_code(callbacks: list[str], offset: i32) -> str:
    lines: list[str] = []
    for i in range(len(callbacks)):
        ty = callbacks[i]
        types = library_callback_types(ty)
        params = types[:-1]
        ret = types[-1]
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
        lines.append("(func " + prefix + ".step (type " + heap + ".sig) (param $env (ref $env<>))" + typed + result + " (call " + prefix + ".host (struct.get " + env + " 0 (ref.cast (ref " + env + ") (local.get $env)))" + values + "))")
        lines.append(library_host_wrapper(LibraryExport("callback", prefix[1:] + ".host", ["HostObject"] + params, ret), offset + i))
        lines.append("(func " + prefix + ".invoke (param $closure (ref " + heap + "))" + typed + result + " (call_ref " + heap + ".sig (struct.get " + heap + " $env (local.get $closure))" + values + " (struct.get " + heap + " $fn (local.get $closure))))")
        lines.append(library_sync_export(LibraryExport("callback", prefix[1:] + ".invoke", [], ret), head, args))
    return "\n".join(lines)


def library_host_imports(hosts: list[LibraryExport], objects: bool = False) -> str:
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
            lines.append('(import "snek_host_async" ' + json_string(item.name) + ' (func $__snek_host_' + str(index) + params + '))')
            lines.append('(import "snek_host_result" ' + json_string(item.name) + ' (func $__snek_result_' + str(index) + result + '))')
        else:
            lines.append('(import "snek_host" ' + json_string(item.name) + ' (func $__snek_host_' + str(index) + params + result + '))')
    for item in hosts:
        if item.heap != "":
            lines.append('(import "snek_async" "status" (func $__snek_async_status (result i32)))')
            lines.append('(import "snek_async" "error" (func $__snek_async_error (result i32 i32)))')
            break
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
    if item.ret == "HostObject" or item.ret.startswith("Callable["): result_type = library_nullable(item.ret)
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
    start += " (call $__snek_host_" + str(index) + args + ") (global.set $__snek_bump (local.get $mark))"
    start += " (struct.set " + B + " $pc (local.get $a) (i32.const 1)) (struct.set " + B + " $busy (local.get $a) (i32.const 0)) (return (i32.const 1))"
    default = "" if item.ret == "None" else " (ref.null $str)" if item.ret == "str" else " (" + library_type(item.ret) + ".const 0)"
    if item.ret in ("HostObject", "HostObject | None"): default = " (ref.null extern)"
    if item.ret.startswith("Callable["): default = " (ref.null " + library_callback_heap(item.ret) + ")"
    finish = "(struct.set " + B + " $pc (local.get $a) (i32.const -1)) (struct.set " + B + " $busy (local.get $a) (i32.const 0))"
    take = "(call $__snek_result_" + str(index) + ")"
    if item.ret == "str":
        take += " (local.set $len) (local.set $ptr)"
        take += " (struct.set " + B + " $value (local.get $a) (call $__snek_read (local.get $ptr) (local.get $len)))"
    elif item.ret != "None":
        take = "(struct.set " + B + " $value (local.get $a) " + take + ")"
    return ("(type " + F + " (sub final " + B + " (struct " + fields + extra + ")))\n"
            + "(elem declare func " + F + ".step)\n"
            + "(func $" + item.target + params + " (result (ref " + B + ")) (struct.new " + F + " (i32.const 0) (i32.const 0) (i32.const -1) (i32.const 0) (i32.const 0) (ref.func " + F + ".step)" + default + values + "))\n"
            + "(func " + F + ".step (type " + B + ".sig) (param $a (ref " + B + ")) (param $error (ref null $BaseException)) (result i32)"
            + " (local $mark i32) (local $ptr i32) (local $len i32) (local $message (ref $str))" + locals
            + " (if (i32.eqz (ref.is_null (local.get $error))) (then " + finish + " (throw $snek.exc (ref.as_non_null (local.get $error)))))"
            + " (if (i32.eqz (struct.get " + B + " $pc (local.get $a))) (then " + start + ")) "
            + finish + " (local.set $mark (global.get $__snek_bump))"
            + " (if (i32.lt_s (call $__snek_async_status) (i32.const 0)) (then (call $__snek_async_error) (local.set $len) (local.set $ptr)"
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
    lines.append('(func (export "__snek_step_' + item.name + '")' + parameter + ' (result i32) (local $pending i32)' + catch
                 + "(local.set $pending (call " + B + ".advance " + value + ")) (call $rt.flush) (return (local.get $pending))) (unreachable))) (unreachable))")
    result = ""
    body = ""
    if item.ret != "None":
        result = " (result " + library_type(item.ret) + ")"
        body = "(struct.get " + B + " $value " + value + ")"
        if item.ret == "str": body = "(call $__snek_write (ref.as_non_null " + body + "))"
        elif item.ret == "HostObject" or item.ret.startswith("Callable["): body = "(ref.as_non_null " + body + ")"
    lines.append('(func (export "__snek_result_' + item.name + '")' + parameter + result + " " + body + ")")
    return "\n".join(lines)


def library_code(exports: list[LibraryExport], hosts: list[LibraryExport]) -> str:
    if len(exports) == 0:
        raise ValueError("--library requires at least one --export FUNCTION")
    callbacks = library_callbacks(exports, hosts)
    lines: list[str] = [emit_library_runtime(), library_callback_code(callbacks, len(hosts))]
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
        callback_signatures.append("[" + json_string(ty) + ",[" + ",".join(args_json) + "]," + json_string(types[-1]) + "]")
    js = "// Generated by Snek. ES module; do not edit.\nconst signatures = [" + ",".join(signatures) + "];\nconst hostSignatures = [" + ",".join(host_signatures) + "];\nconst callbackSignatures = [" + ",".join(callback_signatures) + "];\n" + emit_library_javascript()
    dts = "// Generated by Snek.\nexport interface SnekLibrary {\n" + "\n".join(declarations) + "\n}\n"
    dts += "export interface SnekImports {\n" + "\n".join(host_declarations) + "\n}\n"
    dts += "export interface LibraryOptions { stdout?: (text: string) => void; imports" + ("" if len(hosts) > 0 else "?") + ": SnekImports; }\n"
    dts += "export class SnekError extends Error {}\n"
    dts += "export class SnekCompatibilityError extends Error {}\nexport class SnekLoadError extends Error {}\n"
    dts += "export function assertSupported(): void;\n"
    dts += "export function instantiate(source: BufferSource | WebAssembly.Module | Response | URL | string, options" + ("" if len(hosts) > 0 else "?") + ": LibraryOptions): Promise<SnekLibrary>;\n"
    lines.append(";; SNEK-BINDINGS " + "{\"js\":" + json_string(js) + ",\"dts\":" + json_string(dts) + "}")
    return "\n".join(lines)
