"""Owned whole-module assembly and closed-world protocol generation."""
from __future__ import annotations
from snek import i32
from bootstrap.declarations import DeclType
from bootstrap.bodies import BodyChecker, BodyExpr, BodyStmt, BodyFunction, inner_type
from bootstrap.parser import missing
from bootstrap.emit_data import emit_component_source, emit_input_source
from bootstrap.emit_program import SourceEmitter
from bootstrap.emit_support import EmitUnicode
from bootstrap.emit_runtime import emit_collection_helpers, emit_keyed_builtin, emit_byte_io
from bootstrap.emit_host import emit_host_helpers, emit_host_core_imports, emit_io_helpers, emit_component_imports
from bootstrap.emit_suspension import OwnedMachine, owned_has_await, owned_machine_types, owned_machine_helpers, owned_runtime_machines, owned_runtime_machine
from bootstrap.tasks import wait_all_code
from bootstrap.library import LibraryExport, LibraryClass, library_target, library_code, library_host_imports, library_host_wrapper, library_callbacks, library_callback_imports, library_callback_async, library_lists, library_dicts, library_dict_operations, library_tuples, library_boundary_types


def owned_library_type(ty: DeclType) -> str:
    inner = inner_type(ty)
    if inner.kind == "class": return "class:" + inner.name + (" | None" if ty.kind == "optional" else "")
    if ty.kind == "optional": return owned_library_type(ty.args[0]) + " | None"
    if ty.kind == "list": return "list[" + owned_library_type(ty.args[0]) + "]"
    if ty.kind == "dict": return "dict[" + owned_library_type(ty.args[0]) + ", " + owned_library_type(ty.args[1]) + "]"
    if ty.kind == "tuple":
        parts: list[str] = []
        for arg in ty.args: parts.append(owned_library_type(arg))
        return "tuple[" + (", ".join(parts) if len(parts) > 0 else "()") + "]"
    if ty.kind == "Coroutine": return "Coroutine[None, None, " + owned_library_type(ty.args[0]) + "]"
    if ty.kind == "Callable":
        params: list[str] = []
        for arg in ty.args[:-1]: params.append(owned_library_type(arg))
        return "Callable[[" + ", ".join(params) + "], " + owned_library_type(ty.args[-1]) + "]"
    return ty.display()


def owned_library_collections(e: SourceEmitter, ty: DeclType) -> None:
    if ty.kind == "dict":
        for op in library_dict_operations(): e.operation(ty, op)
    for arg in ty.args: owned_library_collections(e, arg)


def emit_closures(e: SourceEmitter) -> str:
    lines: list[str] = []
    if len(e.closure_refs) != 0:
        names: list[str] = []
        for name in sorted(e.closure_refs): names.append("$" + name)
        lines.append("(elem declare func " + " ".join(names) + ")")
    for name, ty in e.function_refs.items():
        heap = e.types.heap(ty)
        args = ""
        values = ""
        for i in range(len(ty.args) - 1):
            args += f" (param $a{i} {e.types.valtype(ty.args[i])})"
            values += f" (local.get $a{i})"
        ret = ty.args[-1]
        result = f" (result {e.types.valtype(ret)})" if ret.kind != "None" else ""
        lines.append(f"(func $callable.wrap.{name} (type {heap}.sig) (param $env (ref $env<>)){args}{result} (call ${name}{values}))")
        lines.append(f"(global $callable.value.{name} (ref {heap}) (struct.new {heap} (ref.func $callable.wrap.{name}) (struct.new $env<>)))")
    return "\n".join(lines)


def emit_protocols(e: SourceEmitter) -> str:
    decl = e.checked.decl
    lines: list[str] = []
    for name, pair in e.equalities.items():
        left = inner_type(pair[0]).name
        right = inner_type(pair[1]).name
        lines.append(f"(func {name} (param $a (ref null ${left})) (param $b (ref null ${right})) (param $identity i32) (result i32)")
        lines.append("(if (i32.and (local.get $identity) (ref.eq (local.get $a) (local.get $b))) (then (return (i32.const 1))))")
        for a in decl.class_order[::-1]:
            if not decl.subclass(a, left): continue
            for b in decl.class_order[::-1]:
                if not decl.subclass(b, right): continue
                ac = decl.classes[a]
                bc = decl.classes[b]
                reflected = "__eq__" in bc.methods and ("__eq__" not in ac.methods or (b != a and decl.subclass(b, a)))
                owner = b if reflected else a
                value = "(ref.eq (local.get $a) (local.get $b))"
                if "__eq__" in decl.classes[owner].methods:
                    method = decl.classes[owner].methods["__eq__"]
                    recv = "$b" if reflected else "$a"
                    other = "$a" if reflected else "$b"
                    param = e.types.valtype(method.sig.params[0].ty)
                    value = f"(call ${method.owner}.__eq__ (ref.cast (ref ${method.decl}) (local.get {recv})) (ref.cast {param} (local.get {other})))"
                lines.append(f"(if (i32.and (ref.test (ref ${a}) (local.get $a)) (ref.test (ref ${b}) (local.get $b))) (then (return {value})))")
        for side in range(2):
            if pair[1 - side].kind != "optional": continue
            base = left if side == 0 else right
            recv = "$a" if side == 0 else "$b"
            other = "$b" if side == 0 else "$a"
            for name_ in decl.class_order[::-1]:
                cls = decl.classes[name_]
                if not decl.subclass(name_, base) or "__eq__" not in cls.methods: continue
                method = cls.methods["__eq__"]
                value = f"(call ${method.owner}.__eq__ (ref.cast (ref ${method.decl}) (local.get {recv})) (ref.null {e.types.heap(method.sig.params[0].ty)}))"
                lines.append(f"(if (i32.and (ref.test (ref ${name_}) (local.get {recv})) (ref.is_null (local.get {other}))) (then (return {value})))")
        lines.append("(ref.eq (local.get $a) (local.get $b)))")
    for name in sorted(e.class_strings):
        method = decl.classes[name].methods["__str__"]
        lines.append(f"(func $protocol.str.{name} (param $v (ref ${name})) (result (ref $str)) (call_ref ${method.decl}.__str__.sig (local.get $v) (struct.get ${name}.vt $__str__ (struct.get ${name} $__vt__ (local.get $v)))))")
    lines.append("(func $rt.exc_str (param $v (ref $BaseException)) (result (ref $str))")
    for name in decl.class_order[::-1]:
        cls = decl.classes[name]
        if decl.subclass(name, "BaseException") and "__str__" in cls.methods:
            method = cls.methods["__str__"]
            lines.append(f"(if (ref.test (ref ${name}) (local.get $v)) (then (return (call ${method.owner}.__str__ (ref.cast (ref ${method.decl}) (local.get $v))))))")
    lines.append("(call $rt.exc_str_default (local.get $v)))")
    return "\n".join(lines)


def emit_builders(e: SourceEmitter) -> str:
    lines: list[str] = []
    for shape in sorted(e.builders):
        params = ""
        for i in range(len(shape)):
            ty = "i64" if shape[i] == "i" else "(ref $str)"
            params += f" (param $p{i} {ty})"
        lines.append(f"(func $str.build.{shape}{params} (result (ref $str)) (local $len i32) (local $cp i32) (local $pos i32) (local $out (ref $u8s))")
        for i in range(len(shape)):
            lines.append(f"(local $n{i} i32)" + (f" (local $b{i} (ref $u8s))" if shape[i] == "s" else ""))
        for i in range(len(shape)):
            if shape[i] == "i":
                length = f"(call $rt.i64_str_len (local.get $p{i}))"
                cp = f"(local.get $n{i})"
            else:
                lines.append(f"(local.set $b{i} (struct.get $str $bytes (local.get $p{i})))")
                length = f"(array.len (local.get $b{i}))"
                cp = f"(struct.get $str $cp (local.get $p{i}))"
            lines.append(f"(local.set $n{i} {length}) (local.set $len (i32.add (local.get $len) (local.get $n{i}))) (local.set $cp (call $rt.cp_sum (local.get $cp) {cp}))")
        lines.append("(local.set $out (array.new_default $u8s (local.get $len)))")
        for i in range(len(shape)):
            if shape[i] == "i":
                lines.append(f"(local.set $pos (call $rt.i64_write (local.get $out) (local.get $pos) (local.get $p{i}) (local.get $n{i})))")
            else:
                lines.append(f"(array.copy $u8s $u8s (local.get $out) (local.get $pos) (local.get $b{i}) (i32.const 0) (local.get $n{i})) (local.set $pos (i32.add (local.get $pos) (local.get $n{i})))")
        lines.append("(call $rt.mkstr (local.get $out) (local.get $cp)))")
    return "\n".join(lines)


def emit_types(e: SourceEmitter, classes: list[str]) -> str:
    lines = ["(rec", "(type $traceback<> (struct))", "(type $u8s (array (mut i8)))", "(type $u16s (array (mut i16)))", "(type $u32s (array (mut i32)))", "(type $strs (array (ref $str)))", "(type $ostrs (array (mut (ref null $str))))",
             "(type $str (struct (field $bytes (ref $u8s)) (field $hash (mut i32)) (field $cp (mut i32)) (field $idx (mut (ref null $u32s)))))",
             "(type $buf (struct (field $b (mut (ref $u8s))) (field $n (mut i32)) (field $cp (mut i32))))",
             "(type $io.state (struct (field $fd i32) (field $closed (mut i32))))", "(type $io.stream (struct (field $state (ref $io.state)) (field $buffer (ref null $io.stream))))"]
    e.types.close()
    for name, ty in e.types.lists.items():
        lines.append(f"(type $arr.{name} (array (mut {e.types.array_elem(ty.args[0])})))")
        lines.append(f"(type $list.{name} (struct (field $len (mut i32)) (field $data (mut (ref $arr.{name})))))")
    lines.extend(classes)
    lines.extend(e.types.collection_types())
    lines.append(owned_machine_types(e))
    lines.extend(e.types.closure_types())
    if e.component: lines.append('(type $io.chunk (struct (field $fd i32) (field $bytes (ref $u8s)) (field $next (mut (ref null $io.chunk)))))')
    lines.append(")")
    return "\n".join(lines)


def emit_source_module(checked: BodyChecker, prelude_source: str, component: bool = False, exports: list[str] | None = None, entry: str = "") -> str:
    e = SourceEmitter(checked, component)
    prelude = e.literals.expand(prelude_source)
    funcs: list[str] = []
    host_functions: list[LibraryExport] = []
    for name in checked.decl.class_order:
        cls = checked.decl.classes[name]
        if cls.builtin:
            funcs.append(f"(func ${name}.__new__ (param $__msg (ref null $str)) (result (ref ${name})) (struct.new ${name} (global.get ${name}.vt.inst) (local.get $__msg) (ref.null $str)))")
    main_async = False
    targets: list[str] = []
    if exports is not None:
        for name in exports: targets.append(library_target(name, entry) if entry != "" else name)
    for f in checked.functions:
        if f.sig.host != "" or f.name in targets:
            for param in f.sig.params: owned_library_collections(e, param.ty)
            owned_library_collections(e, f.sig.ret)
        if f.sig.host != "":
            if exports is None or len(exports) == 0 or component:
                raise ValueError("host imports require a --library build")
            params: list[str] = []
            for param in f.sig.params:
                params.append(owned_library_type(param.ty))
                e.types.valtype(param.ty)
            if f.sig.ret.kind != "None": e.types.valtype(f.sig.ret)
            async_host = f.sig.ret.kind == "Coroutine"
            item = LibraryExport(f.sig.host, f.name, params, owned_library_type(f.sig.ret.args[0]) if async_host else owned_library_type(f.sig.ret), e.types.heap(f.sig.ret) if async_host else "")
            funcs.append(library_host_wrapper(item, len(host_functions)))
            host_functions.append(item)
            continue
        if f.name == "snek.main":
            if owned_has_await(f.body):
                # Main's coroutine ABI belongs to this emission, not the
                # checked program (which can be emitted again in either mode).
                main = owned_runtime_machine(f.name, f.sig.params, DeclType("None"), f.body)
                main.locals = f.locals.copy()
                f = main
                main_async = True
            elif component:
                main = BodyFunction("snek.main.body", f.sig)
                main.locals = f.locals.copy()
                main.body = f.body
                funcs.append(e.function(main))
                f = owned_runtime_machine("snek.main", [], DeclType("None"), [BodyStmt("ExprStmt", missing()).add(BodyExpr("Call", DeclType("None"), missing(), "snek.main.body"))])
        funcs.append(OwnedMachine(e).run(f) if f.machine else e.function(f))
    funcs.extend(owned_runtime_machines(e))
    classes = e.types.class_types(checked.decl)
    vtables = e.types.vtable_globals(checked.decl)
    closures = emit_closures(e)
    keyed: list[str] = []
    for name, expr in e.keyed.items():
        keyed.append(emit_keyed_builtin(e, name, expr.args[0].ty.args[0], expr.args[1].ty, expr.text, expr.ty))
    byte_io = emit_byte_io(e) if e.byte_io else ""
    io = emit_io_helpers(e) if len(e.io) != 0 else "(func $snek.sys.reset)"
    host = emit_host_helpers(e)
    component_runtime = ""
    if component:
        component_runtime = emit_component_source()
        component_runtime += e.literals.expand(emit_input_source()) if "stream_read" in e.io else '(func $io.input-pump (param i32 i32 i32) (result i32) (i32.const 0))\n(func $io.input-close)'
    collections = emit_collection_helpers(e)
    protocols = emit_protocols(e)
    for name in e.wait_groups:
        if name in e.wait_results:
            wait_result = e.wait_results[name]
            funcs.append(wait_all_code(name, e.wait_groups[name], e.literals.lit("gather() requires distinct, unawaited coroutines"), wait_result[0], wait_result[1], wait_result[2]))
        else:
            funcs.append(wait_all_code(name, e.wait_groups[name], e.literals.lit("wait_all() requires distinct, unawaited coroutines")))
    builders = emit_builders(e)
    machines = owned_machine_helpers(e)
    literals = e.literals.functions()
    unicode = EmitUnicode()
    unicode_dynamic = checked.unicode_dynamic
    for item in host_functions:
        # Async rejection messages are also arbitrary host strings.
        if item.ret == "str" or item.heap != "": unicode_dynamic = True
    imports = '(import "wasi_snapshot_preview1" "fd_write" (func $wasi.fd_write (param i32 i32 i32 i32) (result i32)))\n(import "wasi_snapshot_preview1" "proc_exit" (func $wasi.proc_exit (param i32)))'
    if e.byte_io or "stream_read" in e.io:
        imports += '\n(import "wasi_snapshot_preview1" "fd_read" (func $wasi.fd_read (param i32 i32 i32 i32) (result i32)))'
    imports = emit_component_imports("stream_read" in e.io, e.features) if component else imports + "\n" + emit_host_core_imports(e.features)
    e.types.close()
    if e.types.host_objects and (exports is None or len(exports) == 0):
        raise ValueError("HostObject requires a --library build")
    callbacks: list[str] = []
    start = '(func $snek.start (export "_start") (call $lib.reset) (call $snek.sys.reset) (call $rt.uncaught (block $uncaught (result (ref $BaseException)) (try_table (catch $snek.exc $uncaught) (call $snek.main)) (call $rt.flush) (return))))'
    if main_async:
        start = '(func $snek.start (export "_start") (local $main (ref $coro<none>)) (call $lib.reset) (call $snek.sys.reset) (call $rt.uncaught (block $uncaught (result (ref $BaseException)) (try_table (catch $snek.exc $uncaught) (local.set $main (call $snek.main)) (loop $run (br_if $run (call $coro<none>.advance (local.get $main))))) (call $rt.flush) (return))))'
    library = ""
    if exports is not None and len(exports) > 0:
        if component or main_async or e.byte_io or len(e.io) != 0 or len(e.features) != 0:
            raise ValueError("library targets support print(), not WASI services or async initialization")
        selected: list[LibraryExport] = []
        for name in exports:
            target = library_target(name, entry)
            found = False
            for function in checked.functions:
                if function.name == (target if entry != "" else name):
                    if function.machine and function.sig.ret.kind != "Coroutine":
                        raise ValueError("library exports cannot be generators: " + name)
                    params: list[str] = []
                    for param in function.sig.params:
                        params.append(owned_library_type(param.ty))
                        if param.ty.kind == "str": unicode_dynamic = True
                    async_export = function.sig.ret.kind == "Coroutine"
                    selected.append(LibraryExport(name, function.name, params, owned_library_type(function.sig.ret.args[0]) if async_export else owned_library_type(function.sig.ret), e.types.heap(function.sig.ret) if async_export else ""))
                    found = True
                    break
            if not found: raise ValueError("library export must name a function defined in the entry module: " + name)
        class_types: list[LibraryClass] = []
        for class_name in checked.decl.class_order:
            class_types.append(LibraryClass(class_name, checked.decl.classes[class_name].base))
        library = library_code(selected, host_functions, class_types)
        callbacks = library_callbacks(selected, host_functions)
        imports += "\n" + library_callback_imports(callbacks, len(host_functions))
        if len(callbacks) > 0 or len(library_lists(selected, host_functions)) > 0 or len(library_dicts(selected, host_functions)) > 0 or len(library_tuples(selected, host_functions)) > 0: unicode_dynamic = True
        if "str | None" in library_boundary_types(selected, host_functions): unicode_dynamic = True
        start = start.replace('(export "_start")', '(export "__snek_init")')
    # Host strings are open-world input, even when every source literal is ASCII.
    imports += "\n" + library_host_imports(host_functions, e.types.host_objects, library_callback_async(callbacks))
    unicode.build(e.literals.texts, checked.unicode_flags, checked.unicode_cases, checked.unicode_decimal, unicode_dynamic, checked.unicode_extra)
    return "\n".join(["(module", imports, emit_types(e, classes), "(tag $snek.exc (param (ref $BaseException)))", '' if component else '(memory (export "memory") 1)', unicode.wat(), prelude, byte_io, io, host, literals, collections, "\n".join(keyed), protocols, machines, builders, vtables, closures, "\n".join(funcs), component_runtime if component else start, library, ")"]) + "\n"
