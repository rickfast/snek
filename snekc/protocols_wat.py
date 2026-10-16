"""Closed-world class protocol dispatch, shared by operators and collections."""

from .types import OptionalT, strip_optional


class ProtocolEmitter:
    def object_eq(self, left, right, a, b, *, identity=False):
        from .emit_wat import mangle
        name = f"$protocol.eq.{mangle(left)}.{mangle(right)}"
        self.object_equalities[name] = (left, right)
        return f"(call {name} {a} {b} (i32.const {int(identity)}))"

    def object_equality_helpers(self):
        out = []
        for name, (left, right) in self.object_equalities.items():
            l, r = strip_optional(left).info, strip_optional(right).info
            # Specific subclasses precede bases: the first matching pair is the
            # exact dynamic pair. Unlike containers, direct == has no identity
            # shortcut: an object's __eq__ may deliberately be non-reflexive.
            lines = [f"  (func {name} (param $a (ref null ${l.name})) (param $b (ref null ${r.name}))"
                     " (param $identity i32) (result i32)",
                     "    (if (i32.and (local.get $identity) (ref.eq (local.get $a) (local.get $b)))"
                     " (then (return (i32.const 1))))"]
            for acls in reversed([l, *l.all_subclasses()]):
                for bcls in reversed([r, *r.all_subclasses()]):
                    am, bm = acls.methods.get('__eq__'), bcls.methods.get('__eq__')
                    reflected = bm is not None and (am is None or (bcls is not acls and bcls.is_subclass_of(acls)))
                    method = bm if reflected else am
                    if method is None:
                        value = "(ref.eq (local.get $a) (local.get $b))"
                    else:
                        recv, other = ('$b', '$a') if reflected else ('$a', '$b')
                        param = self.valtype(method.sig.params[0][1])
                        value = (f"(call ${method.owner.name}.__eq__ "
                                 f"(ref.cast (ref ${method.decl.name}) (local.get {recv})) "
                                 f"(ref.cast {param} (local.get {other})))")
                    lines.append(f"    (if (i32.and (ref.test (ref ${acls.name}) (local.get $a)) "
                                 f"(ref.test (ref ${bcls.name}) (local.get $b))) (then (return {value})))")
            for ty, cls, recv, other in ((right, l, '$a', '$b'), (left, r, '$b', '$a')):
                if not isinstance(ty, OptionalT):
                    continue
                for c in reversed([cls, *cls.all_subclasses()]):
                    method = c.methods.get('__eq__')
                    if method is None:
                        continue
                    value = (f"(call ${method.owner.name}.__eq__ "
                             f"(ref.cast (ref ${method.decl.name}) (local.get {recv})) "
                             f"(ref.null {self.heap(method.sig.params[0][1])}))")
                    lines.append(f"    (if (i32.and (ref.test (ref ${c.name}) (local.get {recv})) "
                                 f"(ref.is_null (local.get {other}))) (then (return {value})))")
            lines.append("    (ref.eq (local.get $a) (local.get $b)))")
            out.append('\n'.join(lines))
        return '\n'.join(out)

    def class_str(self, t, value):
        cls = t.info
        if cls.is_subclass_of(self.exc_info()):
            return f"(call $rt.exc_str {value})"
        self.class_strings.add(cls)
        return f"(call $protocol.str.{cls.name} {value})"

    def class_string_helpers(self):
        out = []
        for cls in sorted(self.class_strings, key=lambda c: c.name):
            method = cls.methods['__str__']
            out.append(f"  (func $protocol.str.{cls.name} (param $v (ref ${cls.name})) (result (ref $str))\n"
                       f"    (call_ref ${method.decl.name}.__str__.sig (local.get $v) "
                       f"(struct.get ${cls.name}.vt $__str__ (struct.get ${cls.name} $__vt__ (local.get $v)))))")
        # Exception messages use the same dispatch, including uncaught errors.
        lines = ["  (func $rt.exc_str (param $v (ref $BaseException)) (result (ref $str))"]
        for cls in reversed(self.m.classes):
            if not cls.is_subclass_of(self.exc_info()):
                continue
            method = cls.methods.get('__str__')
            if method:
                lines.append(f"    (if (ref.test (ref ${cls.name}) (local.get $v)) "
                             f"(then (return (call ${method.owner.name}.__str__ "
                             f"(ref.cast (ref ${method.decl.name}) (local.get $v))))))")
        lines.append("    (call $rt.exc_str_default (local.get $v)))")
        return '\n'.join([*out, *lines])
