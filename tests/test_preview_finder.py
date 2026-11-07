import json
import subprocess
import sys
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from urllib.parse import unquote, urlparse

from b4dcad.cli import PreviewHandler, PreviewServer
from b4dcad.preview_finder import (
    DEFAULT_COPY_DIRECTORY,
    FINDER_COPY_SCRIPT,
    copy_file_to_finder,
    finder_copy_command,
    write_copy_file,
)


class FinderClipboardTest(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "darwin", "Requires macOS AppKit")
    def test_native_file_url_and_legacy_file_list_round_trip(self):
        # Use private pasteboards exclusively: never overwrite the user's clipboard.
        preflight = """ObjC.import("AppKit");
var board = $.NSPasteboard.pasteboardWithUniqueName;
var available = false;
try { available = board.setStringForType("probe", "public.utf8-plain-text"); }
finally { board.releaseGlobally; }
available ? "available" : "unavailable";
"""
        available = subprocess.run(
            ["/usr/bin/osascript", "-l", "JavaScript", "-"],
            input=preflight,
            text=True,
            capture_output=True,
            timeout=15,
        )
        if available.returncode or available.stdout.strip() != "available":
            self.skipTest(
                "macOS pasteboard service is unavailable (for example, in a sandbox)"
            )
        self.assertEqual(FINDER_COPY_SCRIPT.count("generalPasteboard()"), 1)
        script = FINDER_COPY_SCRIPT.replace(
            "generalPasteboard()", "pasteboardWithName:(item 2 of argv)"
        )
        name = "b4dcad.tests." + uuid.uuid4().hex
        readback = """ObjC.import("AppKit");
function run(argv) {
    var board = $.NSPasteboard.pasteboardWithName(argv[0]);
    try {
        return JSON.stringify({
            types: ObjC.deepUnwrap(board.types),
            url: ObjC.unwrap(board.stringForType("public.file-url")),
            paths: ObjC.deepUnwrap(board.propertyListForType("NSFilenamesPboardType"))
        });
    } finally { board.releaseGlobally; }
}
"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'quote " \\\n模型 #%.stl'
            path.write_bytes(b"test file")
            try:
                subprocess.run(
                    ["/usr/bin/osascript", "-", str(path), name],
                    input=script,
                    text=True,
                    capture_output=True,
                    check=True,
                    timeout=15,
                )
            finally:
                result = subprocess.run(
                    ["/usr/bin/osascript", "-l", "JavaScript", "-", name],
                    input=readback,
                    text=True,
                    capture_output=True,
                    check=True,
                    timeout=15,
                )
            data = json.loads(result.stdout)
            self.assertIn("public.file-url", data["types"])
            self.assertIn("NSFilenamesPboardType", data["types"])
            url = urlparse(data["url"])
            self.assertEqual(url.scheme, "file")
            self.assertEqual(unquote(url.path), str(path))
            self.assertEqual(data["paths"], [str(path)])

    def test_platform_and_executable_determine_support(self):
        for platform, executable, supported in (
            ("darwin", True, True),
            ("darwin", False, False),
            ("linux", True, False),
            ("win32", True, False),
        ):
            with (
                self.subTest(platform=platform, executable=executable),
                patch("b4dcad.preview_finder.sys.platform", platform),
                patch("b4dcad.preview_finder.os.access", return_value=executable),
            ):
                self.assertEqual(finder_copy_command() is not None, supported)

    def test_path_is_one_argument_and_never_inserted_into_script_or_shell(self):
        path = Path('/tmp/quoted "\\\nend run\ndo shell script "echo unsafe".stl')
        with patch("b4dcad.preview_finder.subprocess.run") as run:
            copy_file_to_finder(path, "/usr/bin/osascript")
        self.assertEqual(run.call_args.args[0], ["/usr/bin/osascript", "-", str(path)])
        self.assertEqual(run.call_args.kwargs["input"], FINDER_COPY_SCRIPT)
        self.assertNotIn(str(path), run.call_args.kwargs["input"])
        self.assertFalse(run.call_args.kwargs.get("shell", False))
        self.assertTrue(run.call_args.kwargs["check"])

    def test_finder_errors_and_timeout_are_reported(self):
        for error, message in (
            (
                subprocess.CalledProcessError(
                    1, "osascript", stderr="automation denied"
                ),
                "automation denied",
            ),
            (subprocess.TimeoutExpired("osascript", 15), "timed out"),
        ):
            with (
                self.subTest(error=error),
                patch("b4dcad.preview_finder.subprocess.run", side_effect=error),
                self.assertRaisesRegex(RuntimeError, message),
            ):
                copy_file_to_finder(Path("/tmp/model.stl"), "/usr/bin/osascript")

    def test_output_names_cannot_escape_directory_and_fit_unicode_filename_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "output"
            for script, name in (
                ("...py", '../../escape"\n'),
                ("模型" * 100 + ".py", "组件" * 100),
            ):
                path = write_copy_file(b"stl bytes", target, script, name)
                self.assertEqual(path.parent, target.resolve())
                self.assertEqual(path.suffix, ".stl")
                self.assertLessEqual(len(path.name.encode("utf-8")), 255)
                self.assertEqual(path.read_bytes(), b"stl bytes")

    def test_atomic_output_replaces_symlink_without_writing_its_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "output"
            target.mkdir()
            outside = root / "outside"
            outside.write_bytes(b"unchanged")
            (target / "model-part.stl").symlink_to(outside)
            path = write_copy_file(b"new mesh", target, "model.py", "part")
            self.assertEqual(outside.read_bytes(), b"unchanged")
            self.assertFalse(path.is_symlink())
            self.assertEqual(path.read_bytes(), b"new mesh")
            self.assertFalse(list(target.glob(".b4dcad-copy-*")))


class CopyModelTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.script = self.root / "model.py"
        self.script.write_text(
            "from b4dcad import box\npart = box(1, 2, 3)\nshow_all = part.move(x=3)\n"
        )
        with patch("b4dcad.cli.finder_copy_command", return_value="/usr/bin/osascript"):
            self.server = PreviewServer(
                ("127.0.0.1", 0), PreviewHandler, str(self.script), poll_interval=3600
            )
        self.addCleanup(self.server.server_close)

    def request_data(self, name="part"):
        state = self.server.state()
        return {
            "name": name,
            "source": state["source"],
            "tree_version": state["tree_version"],
        }

    def test_selected_model_is_written_before_finder_receives_quoted_directory(self):
        target = self.root / 'quotes "\\\nend run\ndo shell script "echo unsafe"'
        self.server.write_stl = str(target)
        expected = self.server.stl_bytes("part")

        def run(command, **kwargs):
            path = Path(command[-1])
            self.assertEqual(path.parent, target)
            self.assertEqual(path.read_bytes(), expected)
            self.assertEqual(kwargs["input"], FINDER_COPY_SCRIPT)
            self.assertNotIn(str(target), kwargs["input"])

        with patch("b4dcad.preview_finder.subprocess.run", side_effect=run) as finder:
            result = self.server.copy_model(**self.request_data())
        finder.assert_called_once()
        self.assertEqual(Path(result["path"]).name, "model-part.stl")

    def test_fallback_directory_and_preview_only_model(self):
        self.assertEqual(DEFAULT_COPY_DIRECTORY, "/tmp/stl")
        target = self.root / "fallback"
        with (
            patch("b4dcad.cli.DEFAULT_COPY_DIRECTORY", str(target)),
            patch("b4dcad.cli.copy_file_to_finder") as finder,
        ):
            result = self.server.copy_model(**self.request_data("show_all"))
        path = Path(result["path"])
        self.assertEqual(path.parent, target)
        self.assertEqual(path.read_bytes(), self.server.stl_bytes("show_all"))
        finder.assert_called_once_with(path, "/usr/bin/osascript")

    def test_stale_or_unknown_model_is_rejected_before_writing(self):
        valid = self.request_data()
        for changes in (
            {"name": "missing"},
            {"name": []},
            {"source": "other.py"},
            {"tree_version": valid["tree_version"] + 1},
        ):
            with (
                self.subTest(changes=changes),
                patch("b4dcad.cli.write_copy_file") as write,
                patch("b4dcad.cli.copy_file_to_finder") as finder,
                self.assertRaises(ValueError),
            ):
                self.server.copy_model(**(valid | changes))
            write.assert_not_called()
            finder.assert_not_called()

    def test_unsupported_backend_disables_capability_and_rejects_copy(self):
        self.server._copy_command = None
        self.assertFalse(self.server.state()["can_copy_model"])
        with (
            patch("b4dcad.cli.write_copy_file") as write,
            self.assertRaises(NotImplementedError),
        ):
            self.server.copy_model(**self.request_data())
        write.assert_not_called()

    def test_http_capability_copy_and_failure_responses(self):
        self.server.write_stl = str(self.root / "output")
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{self.server.server_port}"

        def post(data):
            return urlopen(
                Request(
                    base + "/copy-model",
                    json.dumps(data).encode(),
                    {"Content-Type": "application/json"},
                ),
                timeout=3,
            )

        try:
            with urlopen(base + "/state.json", timeout=3) as response:
                self.assertTrue(json.load(response)["can_copy_model"])
            with patch("b4dcad.cli.copy_file_to_finder") as finder:
                with post(self.request_data()) as response:
                    path = Path(json.load(response)["path"])
                self.assertEqual(path.read_bytes(), self.server.stl_bytes("part"))
                finder.assert_called_once()
            with patch(
                "b4dcad.cli.copy_file_to_finder",
                side_effect=RuntimeError("automation denied"),
            ):
                with self.assertRaises(HTTPError) as caught:
                    post(self.request_data())
                self.assertEqual(caught.exception.code, 500)
                self.assertIn("automation denied", json.load(caught.exception)["error"])
                caught.exception.close()
            self.server._copy_command = None
            with self.assertRaises(HTTPError) as caught:
                post(self.request_data())
            self.assertEqual(caught.exception.code, 501)
            caught.exception.close()
        finally:
            self.server.shutdown()
            thread.join()


if __name__ == "__main__":
    unittest.main()
