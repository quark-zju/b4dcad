import io
import json
import os
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from b4dcad import box
from b4dcad.cli import PreviewHandler, PreviewServer
from b4dcad.preview_files import PreviewFiles


class PreviewFilesTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve() / "repo"
        self.root.mkdir()

    def write(self, name, text="import b4dcad\n"):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def file_paths(self, tree):
        paths = []
        for entry in tree:
            if "children" in entry:
                paths.extend(self.file_paths(entry["children"]))
            else:
                paths.append(entry["path"])
        return paths

    def test_repository_tree_only_contains_python_and_stays_in_boundary(self):
        self.write(".git/config")
        first = self.write("models/first.py")
        self.write("second.py", "# b4dcad is mentioned in a comment\n")
        self.write("unrelated.py", "print('hello')\n")
        self.write("unused/helper.py", "print('hello')\n")
        self.write("uppercase.py", "import B4DCAD\n")
        self.write("models/notes.txt")
        self.write("empty/notes.md")
        self.write(".git/hidden.py")
        self.write("nested/.git", "gitdir: elsewhere")
        self.write("nested/other.py")
        outside = self.root.parent / "outside.py"
        outside.write_text("")
        (self.root / "escape.py").symlink_to(outside)
        (self.root / "linked-directory").symlink_to(
            self.root.parent, target_is_directory=True
        )

        files = PreviewFiles(first)
        self.assertEqual(files.root, self.root)
        tree = files.tree(first)
        self.assertEqual(tree["selected"], "models/first.py")
        self.assertEqual(
            self.file_paths(tree["files"]), ["models/first.py", "second.py"]
        )
        self.assertEqual(files.resolve("second.py"), self.root / "second.py")
        for name in (
            "../outside.py",
            "escape.py",
            "nested/other.py",
            ".git/hidden.py",
            ".",
            "missing.py",
            "models/notes.txt",
        ):
            with self.subTest(name=name), self.assertRaises(ValueError):
                files.resolve(name)

    def test_worktree_marker_and_nearest_nested_repository(self):
        self.write(".git", "gitdir: /some/repository/.git/worktrees/name")
        first = self.write("models/first.py")
        self.write("second.py")
        self.assertEqual(PreviewFiles(first).root, self.root)
        nested = self.write("nested/.git", "gitdir: elsewhere").parent
        inside = self.write("nested/inside.py")
        self.assertEqual(PreviewFiles(inside).root, nested)
        with self.assertRaises(ValueError):
            PreviewFiles(inside).resolve("../second.py")

    def test_without_git_only_current_directory_is_selectable(self):
        first = self.write("models/first.py")
        self.write("models/second.py")
        self.write("models/unrelated.py", "print('hello')\n")
        self.write("models/subdir/third.py")
        self.write("outside.py")
        files = PreviewFiles(first)
        self.assertEqual(files.root, first.parent)
        self.assertFalse(files.recursive)
        self.assertEqual(
            self.file_paths(files.tree(first)["files"]), ["first.py", "second.py"]
        )
        for name in ("../outside.py", "subdir/third.py", str(first), None):
            with self.subTest(name=name), self.assertRaises(ValueError):
                files.resolve(name)


class PreviewFileSwitchTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        (self.root / ".git").mkdir()
        self.first = self.write(
            "a/model.py",
            "from preview_switch_helper import size\nfrom b4dcad import box\npart = box(size, 2, 3)\n",
        )
        self.second = self.write(
            "b/model.py",
            "from preview_switch_helper import size\nfrom b4dcad import box\nother = box(size, 2, 3)\n",
        )
        self.first_helper = self.write("a/preview_switch_helper.py", "size = 3\n")
        self.second_helper = self.write("b/preview_switch_helper.py", "size = 7\n")
        self.server = PreviewServer(
            ("127.0.0.1", 0),
            PreviewHandler,
            str(self.first),
            object_name="part",
            poll_interval=3600,
        )
        self.addCleanup(self.close)

    def close(self):
        self.server.server_close()
        self.server._module_tracker._purge()

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def edit(self, path, text):
        stamp = path.stat().st_mtime_ns
        path.write_text(text)
        os.utime(path, ns=(stamp + 1_000_000_000, stamp + 1_000_000_000))

    def test_switch_replaces_models_imports_tree_and_watched_paths(self):
        before = self.server.state()
        old_node = before["tree"]["roots"]["part"]
        state = self.server.select_file("b/model.py")
        self.assertEqual(state["source"], "b/model.py")
        self.assertEqual(state["models"], ["other"])
        self.assertIsNone(state["error"])
        self.assertIsNone(self.server.object_name)
        self.assertEqual(self.server.stl_bytes("other"), box(7, 2, 3).stl())
        self.assertIsNone(self.server.stl_bytes("part"))
        self.assertIsNone(self.server.tree_stl_bytes(before["tree_version"], old_node))
        self.assertGreater(state["version"], before["version"])
        self.assertEqual(
            self.server._watched_paths(), {self.second, self.second_helper}
        )
        self.edit(self.first_helper, "size = 11\n")
        self.assertFalse(self.server.check_for_change())
        self.edit(self.second_helper, "size = 9\n")
        self.assertTrue(self.server.check_for_change())
        self.assertEqual(self.server.stl_bytes("other"), box(9, 2, 3).stl())
        current = self.server.version
        self.server.select_file("b/model.py")
        self.assertEqual(self.server.version, current)

    def test_bad_file_does_not_keep_previous_geometry_and_can_be_fixed(self):
        bad = self.write("bad.py", "raise RuntimeError('broken model')\n")
        with redirect_stdout(io.StringIO()):
            state = self.server.select_file("bad.py")
        self.assertEqual(state["source"], "bad.py")
        self.assertEqual(state["models"], [])
        self.assertEqual(state["tree"]["nodes"], {})
        self.assertIn("broken model", state["error"]["message"])
        self.assertIsNone(self.server.stl_bytes())
        self.assertEqual(self.server._watched_paths(), {bad})
        self.edit(bad, "from b4dcad import box\nfixed = box(4, 5, 6)\n")
        self.assertTrue(self.server.check_for_change())
        self.assertEqual(self.server.model_names(), ["fixed"])

    def test_rejected_selection_leaves_current_file_unchanged(self):
        outside = self.root.parent / (self.root.name + "_outside.py")
        outside.write_text("raise AssertionError('must not execute')\n")
        self.addCleanup(outside.unlink)
        before = self.server.state()
        for name in ("../" + outside.name, "not-a-model.txt", ".", None):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.server.select_file(name)
            self.assertEqual(self.server.state(), before)

    def test_http_file_tree_selection_validation_and_escaped_filename(self):
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{self.server.server_port}"
        try:
            with urlopen(base + "/files.json", timeout=3) as response:
                self.assertEqual(json.load(response)["selected"], "a/model.py")
            request = Request(
                base + "/select-file",
                json.dumps({"path": "b/model.py"}).encode(),
                {"Content-Type": "application/json"},
            )
            with urlopen(request, timeout=3) as response:
                self.assertEqual(json.load(response)["source"], "b/model.py")
            for body, content_type, code in (
                (b"{}", "text/plain", 415),
                (b"[]", "application/json", 400),
                (b"{", "application/json", 400),
            ):
                with self.assertRaises(HTTPError) as error:
                    urlopen(
                        Request(
                            base + "/select-file", body, {"Content-Type": content_type}
                        ),
                        timeout=3,
                    )
                self.assertEqual(error.exception.code, code)
                error.exception.close()
            self.write("<other>.py", "from b4dcad import box\npart = box(1, 2, 3)\n")
            self.server.select_file("<other>.py")
            with urlopen(base + "/", timeout=3) as response:
                self.assertIn("&lt;other&gt;.py", response.read().decode())
        finally:
            self.server.shutdown()
            thread.join()


if __name__ == "__main__":
    unittest.main()
