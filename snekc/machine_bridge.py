"""Transport to Snek suspension lowering and frame emission, with no fallback.

Declaration/type objects and numeric constants travel as opaque handles. The
guest rearranges IR; this adapter rehydrates it using the original objects.
The emission adapter supplies ordinary code fragments and resolved type names,
leaving suspension frame, dispatch and protocol WAT generation to the guest.
"""
from __future__ import annotations

from dataclasses import fields
import json
from pathlib import Path
from types import SimpleNamespace

from . import ir, runner
from .machines import State
from .types import BOOL, NONE, ClassT, CoroutineT, IteratorT, OptionalT


class MachineWire:
    def __init__(self):
        self.objects = []
        self.handles = {}
        self.types = {}
        self.nodes = {name: cls for name, cls in vars(ir).items()
                      if isinstance(cls, type) and (issubclass(cls, (ir.Expr, ir.Stmt)) or cls is ir.Handler)}

    def opaque(self, value):
        from .types import Type
        # Equal types must share a handle (especially independently constructed
        # NONE/result types). Declaration identities are never compared by value.
        if isinstance(value, Type):
            value = self.types.setdefault(value, value)
        key = id(value)
        if key not in self.handles:
            self.handles[key] = len(self.objects)
            self.objects.append(value)
        return {"_type": "Opaque", "id": self.handles[key]}

    def pack(self, value):
        if value is None or isinstance(value, (str, bool)):
            return value
        if isinstance(value, list):
            return [self.pack(item) for item in value]
        if isinstance(value, tuple):
            return {"_type": "Tuple", "values": [self.pack(item) for item in value]}
        if isinstance(value, (ir.Expr, ir.Stmt, ir.Handler)):
            return {"_type": type(value).__name__, **{f.name: self.pack(getattr(value, f.name)) for f in fields(value)}}
        return self.opaque(value)

    def unpack(self, value):
        if isinstance(value, list):
            return [self.unpack(item) for item in value]
        if not isinstance(value, dict):
            return value
        kind = value.get("_type")
        if kind == "Opaque":
            index = value["id"]
            if type(index) is not int or not 0 <= index < len(self.objects):
                raise ValueError("invalid machine opaque handle")
            return self.objects[index]
        if kind == "Tuple":
            return tuple(self.unpack(item) for item in value["values"])
        if kind not in self.nodes:
            raise ValueError(f"unknown machine IR node: {kind!r}")
        return self.nodes[kind](**{k: self.unpack(v) for k, v in value.items() if k != "_type"})

    def request(self, f, exc):
        return {"_type": "MachineRequest", "version": 1,
                "none": self.opaque(NONE), "boolean": self.opaque(BOOL),
                "exception": self.opaque(ClassT(exc)),
                "result": self.opaque(f.ret.elem if isinstance(f.ret, CoroutineT) else NONE),
                "locals": [{"_type": "Local", "name": n, "ty": self.opaque(t)}
                           for n, t in (dict(f.params) | f.locals).items()],
                "body": self.pack(f.body)}

    def graph(self, value):
        if value.get("_type") != "MachineGraph" or value.get("version") != 1:
            raise ValueError("unsupported machine graph version")
        states = []
        for s in value["states"]:
            if s.get("_type") != "State" or s["kind"] not in ("stmt", "branch", "goto", "done", "fail", "await", "yield"):
                raise ValueError("invalid machine state")
            states.append(State(self.unpack(s["stmt"]), s["next"], s["error"], s["other"], s["kind"]))
        for edge in [value[k] for k in ("entry", "end", "fail")]:
            if type(edge) is not int or not 0 <= edge < len(states):
                raise ValueError("invalid machine entry edge")
        for state in states:
            for edge in (state.next, state.error, state.other):
                if type(edge) is not int or not -1 <= edge < len(states):
                    raise ValueError("invalid machine state edge")
        return SimpleNamespace(states=states, locals={v["name"]: self.unpack(v["ty"]) for v in value["locals"]},
                               result=self.unpack(value["result"]), entry=value["entry"], end=value["end"], fail=value["fail"])


class WasmGraphFactory:
    def __init__(self, binary: Path):
        self.binary = Path(binary)

    def __call__(self, f, exc):
        wire = MachineWire()
        request = json.dumps(wire.request(f, exc), ensure_ascii=False).encode("utf-8")
        try:
            result = runner.run_binary(self.binary, stdin=request)
        except OSError as error:
            raise ValueError(f"cannot run Snek machine lowerer {self.binary}: {error}") from error
        if result.code or result.stderr:
            raise ValueError("Snek machine lowering failed: " + result.stderr.decode("utf-8", errors="replace"))
        return wire.graph(json.loads(result.stdout))


class WasmMachineBackend:
    """Adapt emission inputs; suspension-specific WAT is generated in Wasm.

Ordinary expressions/statements, type registration and literal allocation are
still host responsibilities. Keep their ordering identical to the reference
emitter, including temporaries and demand-generated runtime dependencies.
"""
    def __init__(self, binary: Path):
        self.binary = Path(binary)

    def emit(self, kind, **fields_):
        request = {"_type": kind, "version": 1, **fields_}
        try:
            result = runner.run_binary(self.binary, stdin=json.dumps(request, ensure_ascii=False).encode("utf-8"))
        except OSError as error:
            raise ValueError(f"cannot run Snek machine emitter {self.binary}: {error}") from error
        if result.code or result.stderr:
            raise ValueError("Snek machine emission failed: " + result.stderr.decode("utf-8", errors="replace"))
        response = json.loads(result.stdout)
        if (not isinstance(response, dict) or response.get("_type") != "MachineWAT"
                or type(response.get("version")) is not int or response["version"] != 1
                or not isinstance(response.get("wat"), str)):
            raise ValueError("unsupported machine emission response")
        return response["wat"]

    def type(self, emitter, ty):
        return {"_type": "WasmType", "none": ty == NONE, "ref": ty.is_ref,
                "optional": isinstance(ty, OptionalT),
                "heap": emitter.heap(ty) if ty.is_ref else "",
                "storage": emitter.storage(ty) if ty != NONE else "",
                "value": emitter.valtype(ty) if ty != NONE else ""}

    def machine(self, emitter, ty):
        return {"_type": "MachineType", "heap": emitter.heap(ty),
                "elem": self.type(emitter, ty.elem), "generator": isinstance(ty, IteratorT)}

    def locals(self, emitter, locals_):
        return [{"_type": "FrameLocal", "name": n, "type": self.type(emitter, t)} for n, t in locals_]

    def frame(self, emitter, f, g):
        from .emit_wat import FnState
        machine = self.machine(emitter, f.ret)
        locals_ = self.locals(emitter, g.locals.items())
        emitter.fn = FnState(f)
        emitter.fn.nonnull.clear()
        states = []
        stop_message = ""
        try:
            for state in g.states:
                s = state.stmt
                entry = {"_type": "FrameState", "kind": state.kind, "next": state.next,
                         "error": state.error, "other": state.other, "statement": s is not None, "code": ""}
                if state.kind == "fail":
                    message = "generator raised StopIteration" if isinstance(f.ret, IteratorT) else "coroutine raised StopIteration"
                    stop_message = emitter.lit(message)
                elif state.kind == "branch":
                    entry["code"] = emitter.ex(s.cond)
                elif state.kind == "await":
                    entry["child_type"] = self.machine(emitter, s.operand.ty)
                    entry["code"] = emitter.ex(s.operand)
                    entry["target"] = s.name or ""
                elif state.kind == "yield":
                    entry["code"] = emitter.ex(s.value) if s.value is not None else ""
                elif state.kind not in ("done", "fail") and s is not None:
                    entry["code"] = " ".join(emitter.stmt(s))
                states.append(entry)
            return self.emit("FrameRequest", name=f.name, machine=machine, locals=locals_, states=states,
                             result=g.result != NONE, stop_message=stop_message,
                             self_cast=f.self_cast.name if f.self_cast else "",
                             temps=[{"_type": "Temp", "name": n, "type": t} for n, t in emitter.fn.temps],
                             params=self.locals(emitter, f.params), signature=f.sig_type or "", entry=g.entry, fail=g.fail)
        finally:
            emitter.fn = None

    def types(self, emitter):
        machines = [self.machine(emitter, t) for t in list(emitter.machine_types.values())]
        frames = [{"_type": "FrameType", "name": f.name, "machine": self.machine(emitter, f.ret),
                   "locals": self.locals(emitter, g.locals.items())} for f, g in emitter.machine_graphs]
        return self.emit("MachineTypesRequest", machines=machines, frames=frames).splitlines()

    def helpers(self, emitter):
        machines = []
        for ty in list(emitter.machine_types.values()):
            machine = self.machine(emitter, ty)
            generator = isinstance(ty, IteratorT)
            machine["busy_message"] = emitter.lit("generator already executing" if generator else "coroutine already executing")
            if generator:
                machine["close_message"] = emitter.lit("generator ignored GeneratorExit")
            else:
                machine["reuse_message"] = emitter.lit("cannot reuse already awaited coroutine")
            machines.append(machine)
        return self.emit("MachineHelpersRequest", machines=machines)
