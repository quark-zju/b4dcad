import json
import io
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

from b4dcad import Solid
from b4dcad.cli import (
    PreviewHandler,
    PreviewServer,
    _LocalModuleTracker,
    build_preview_scene,
)
from b4dcad.preview_trace import PreviewTrace


class PreviewTraceTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.script = Path(self.directory.name).resolve() / "model.py"

    def build(self, source):
        self.script.write_text("from b4dcad import box\n" + source)
        return build_preview_scene(str(self.script))

    def test_flatten_only_anonymous_unions(self):
        _, _, tree = self.build(
            "a = box(1, 2, 3); b = box(2, 3, 4); c = box(3, 4, 5)\n"
            "o = a + b + c\nab = a + b\nnested = ab + c\n"
        )
        roots = tree["roots"]
        nodes = tree["nodes"]
        self.assertEqual(nodes[roots["o"]]["children"], [roots[n] for n in "abc"])
        self.assertEqual(nodes[roots["nested"]]["children"], [roots["ab"], roots["c"]])
        self.assertTrue(all(node["names"] for node in nodes.values()))
        self.assertEqual(nodes[roots["ab"]]["children"], [roots["a"], roots["b"]])

    def test_rebindings_same_line_loops_aliases_and_local_returns(self):
        cache, trace, tree = self.build(
            "a = box(1, 1, 1); a = box(2, 2, 2); alias = a\n"
            "for i in range(3):\n    part = box(i + 1, 1, 1)\n"
            "def make():\n    local = box(4, 5, 6)\n    return local\n"
            "result = make()\n"
        )
        a = [event for event in tree["assignments"] if event["name"] == "a"]
        self.assertEqual(len(a), 2)
        self.assertNotEqual(a[0]["node"], a[1]["node"])
        self.assertEqual(a[0]["line"], 2)
        self.assertEqual(trace.stl_bytes(a[1]["node"]), cache["a"])
        self.assertEqual(tree["roots"]["a"], tree["roots"]["alias"])
        self.assertEqual(
            len([event for event in tree["assignments"] if event["name"] == "part"]),
            3,
        )
        local = next(event for event in tree["assignments"] if event["name"] == "local")
        self.assertEqual(local["scope"], "make")
        self.assertEqual(local["node"], tree["roots"]["result"])

    def test_native_manifold_and_anonymous_primitives(self):
        _, trace, tree = self.build(
            "from manifold3d import Manifold\n"
            "raw = Manifold.cube((3, 4, 5))\n"
            "a = box(1, 2, 3)\no = a + box(2, 2, 2)\n"
        )
        raw = next(
            event["node"] for event in tree["assignments"] if event["name"] == "raw"
        )
        self.assertEqual(trace.stl_bytes(raw), Solid(trace.objects[raw]).stl())
        self.assertEqual(
            tree["nodes"][tree["roots"]["o"]]["children"], [tree["roots"]["a"]]
        )
        self.assertIsNone(trace.stl_bytes("missing"))

    def test_globals_closures_and_empty_shapes(self):
        _, trace, tree = self.build(
            "from b4dcad import Solid\n"
            "a = box(1, 1, 1)\n"
            "def update():\n    global a\n    a = box(2, 2, 2)\n"
            "def outer():\n"
            "    captured = box(3, 3, 3)\n"
            "    def inner():\n        nonlocal captured\n        captured = box(4, 4, 4)\n"
            "    inner()\n    return captured\n"
            "update()\nresult = outer()\nempty = Solid()\n"
        )
        self.assertEqual(len([a for a in tree["assignments"] if a["name"] == "a"]), 2)
        self.assertEqual(
            len([a for a in tree["assignments"] if a["name"] == "captured"]), 2
        )
        self.assertEqual(len(trace.stl_bytes(tree["roots"]["empty"])), 84)

    def test_local_imports_are_traced(self):
        helper = self.script.with_name("preview_tree_helper.py")
        helper.write_text("from b4dcad import box\nleft = box(2, 3, 4)\n")
        self.script.write_text(
            "from preview_tree_helper import left\n"
            "from b4dcad import box\nright = box(1, 1, 1)\nresult = left + right\n"
        )
        tracker = _LocalModuleTracker(self.script)
        try:
            _, _, tree = tracker.execute(
                lambda run_name: build_preview_scene(
                    str(self.script), run_name=run_name
                )
            )
            self.assertTrue(any(Path(a["file"]) == helper for a in tree["assignments"]))
            self.assertIn(helper, tracker.dependency_paths())
        finally:
            tracker._purge()

    def test_trace_is_restored_after_failure(self):
        previous = sys.gettrace()
        with self.assertRaisesRegex(RuntimeError, "failed"):
            with PreviewTrace(self.script):
                raise RuntimeError("failed")
        self.assertIs(sys.gettrace(), previous)

    def test_http_tree_geometry_and_failed_rebuild(self):
        self.script.write_text(
            "from b4dcad import box\na = box(1, 2, 3)\n"
            "b = box(2, 3, 4).move(x=5)\nresult = a + b\n"
        )
        server = PreviewServer(
            ("127.0.0.1", 0), PreviewHandler, str(self.script), poll_interval=3600
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_port}"
            with urlopen(base + "/state.json") as response:
                state = json.load(response)
            key = state["tree"]["roots"]["a"]
            with urlopen(f"{base}/tree/{state['tree_version']}/{key}.stl") as response:
                data = response.read()
            self.assertEqual(data, server.stl_bytes("a"))
            with self.assertRaises(HTTPError) as error:
                urlopen(f"{base}/tree/{state['tree_version'] + 1}/{key}.stl")
            self.assertEqual(error.exception.code, 404)
            error.exception.close()
            self.script.write_text("raise RuntimeError('bad build')\n")
            with redirect_stdout(io.StringIO()):
                self.assertIsNone(server.rebuild_cache())
            self.assertEqual(server.state()["tree"], state["tree"])
            self.assertEqual(server.tree_stl_bytes(state["tree_version"], key), data)
            self.script.write_text("from b4dcad import box\na = box(9, 9, 9)\n")
            self.assertIsNotNone(server.rebuild_cache())
            self.assertIsNone(server.tree_stl_bytes(state["tree_version"], key))
            self.assertGreater(server.state()["tree_version"], state["tree_version"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main()
