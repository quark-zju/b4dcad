"""Preview-only assignment history and Solid union provenance."""

import dis
import sys
from pathlib import Path

from manifold3d import Manifold

from .core import Solid


class PreviewTrace:
    def __init__(self, script):
        self.root = Path(script).resolve().parent
        self.library = Path(__file__).resolve().parent
        self.nodes = {}
        self.objects = {}
        self.assignments = []
        self._ids = {}
        self._instructions = {}
        self._files = {}
        self._solid_methods = {
            method.__code__
            for method in vars(Solid).values()
            if hasattr(method, "__code__")
        }

    def node(self, value):
        if not isinstance(value, (Solid, Manifold)):
            return None
        identity = id(value)
        if identity not in self._ids:
            key = str(len(self.nodes))
            self._ids[identity] = key
            self.objects[key] = (
                value  # Retain intermediate shapes and prevent id reuse.
            )
            self.nodes[key] = {
                "id": key,
                "operation": "shape",
                "names": [],
                "children": [],
            }
        return self._ids[identity]

    def _user_file(self, filename):
        if filename not in self._files:
            if filename.startswith("<"):
                self._files[filename] = False
                return False
            path = Path(filename).resolve()
            self._files[filename] = path.is_relative_to(
                self.root
            ) and not path.is_relative_to(self.library)
        return self._files[filename]

    def _trace(self, frame, event, arg):
        if frame.f_code is Solid.__add__.__code__:
            left, right = frame.f_locals["self"], frame.f_locals["other"]

            def union_return(frame, event, result):
                if event == "return" and isinstance(result, Solid):
                    children = [self.node(left), self.node(right)]
                    self.nodes[self.node(result)].update(
                        operation="+", children=children
                    )
                return union_return

            frame.f_trace_lines = False
            return union_return

        if frame.f_code in self._solid_methods:
            source = frame.f_locals.get("self")

            def method_return(frame, event, result):
                if (
                    event == "return"
                    and isinstance(source, Solid)
                    and isinstance(result, Solid)
                    and result is not source
                ):
                    key = self.node(result)
                    # A wrapper such as align() may return a result already traced
                    # inside move(). Preserve that more direct origin.
                    if self.nodes[key]["operation"] == "shape":
                        self.nodes[key].update(
                            operation=frame.f_code.co_name,
                            children=[self.node(source)],
                        )
                return method_return

            frame.f_trace_lines = False
            return method_return

        if not self._user_file(frame.f_code.co_filename):
            return None
        instructions = self._instructions.setdefault(frame.f_code, None)
        if instructions is None:
            instructions = {
                item.offset: item for item in dis.get_instructions(frame.f_code)
            }
            self._instructions[frame.f_code] = instructions
        pending = None
        frame.f_trace_opcodes = True
        frame.f_trace_lines = False

        def assignments(frame, event, arg):
            nonlocal pending
            if pending is not None:
                names, line, global_name = pending
                pending = None
                if event != "exception":
                    namespace = frame.f_globals if global_name else frame.f_locals
                    for name in names:
                        key = self.node(namespace.get(name))
                        if key is not None:
                            node = self.nodes[key]
                            if name not in node["names"]:
                                node["names"].append(name)
                            self.assignments.append(
                                {
                                    "node": key,
                                    "name": name,
                                    "file": str(
                                        Path(frame.f_code.co_filename).resolve()
                                    ),
                                    "line": line,
                                    "scope": frame.f_code.co_name,
                                }
                            )
            if event == "opcode":
                instruction = instructions.get(frame.f_lasti)
                names = ()
                if instruction and instruction.opname in {
                    "STORE_FAST",
                    "STORE_NAME",
                    "STORE_GLOBAL",
                    "STORE_DEREF",
                }:
                    names = (instruction.argval,)
                elif instruction and instruction.opname in {
                    "STORE_FAST_STORE_FAST",
                    "STORE_FAST_LOAD_FAST",
                }:
                    # Python 3.13+ packs two local indexes into one instruction.
                    indexes = [instruction.arg >> 4]
                    if instruction.opname == "STORE_FAST_STORE_FAST":
                        indexes.append(instruction.arg & 15)
                    names = tuple(frame.f_code.co_varnames[index] for index in indexes)
                if names:
                    pending = (
                        names,
                        frame.f_lineno,
                        instruction.opname == "STORE_GLOBAL",
                    )
            return assignments

        return assignments

    def __enter__(self):
        self._previous = sys.gettrace()
        # Python 3.12 requires an opcode-enabled frame before installing the tracer.
        self._frame = sys._getframe()
        self._previous_opcodes = self._frame.f_trace_opcodes
        self._frame.f_trace_opcodes = True
        sys.settrace(self._trace)
        return self

    def __exit__(self, *exc):
        sys.settrace(self._previous)
        self._frame.f_trace_opcodes = self._previous_opcodes
        del self._frame

    def data(self, models):
        roots = {name: self.node(model) for name, model in models.items()}
        for name, key in roots.items():
            if key is not None and name not in self.nodes[key]["names"]:
                self.nodes[key]["names"].append(name)

        def named_children(node):
            result = []
            pending = list(reversed(node["children"]))
            while pending:
                child = self.nodes[pending.pop()]
                if child["names"]:
                    result.append(child["id"])
                else:
                    pending.extend(reversed(child["children"]))
            return result

        return {
            "nodes": {
                key: {**node, "children": named_children(node)}
                for key, node in self.nodes.items()
                if node["names"]
            },
            "roots": roots,
            "assignments": self.assignments,
        }

    def stl_bytes(self, key):
        value = self.objects.get(key)
        if isinstance(value, Manifold):
            value = Solid(value)
        return None if value is None else value.stl()
