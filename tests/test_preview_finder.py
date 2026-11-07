import json
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from b4dcad.cli import PreviewHandler, PreviewServer
from b4dcad.preview_finder import (
    DEFAULT_FINDER_DIRECTORY,
    finder_reveal_command,
    reveal_in_finder,
    write_finder_exports,
)


class FinderExportsTest(unittest.TestCase):
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
                self.assertEqual(finder_reveal_command() is not None, supported)

    def test_reveal_path_is_one_argument_without_shell_or_script(self):
        path = Path('/tmp/quoted "\\\n$(echo unsafe).stl')
        with patch("b4dcad.preview_finder.subprocess.run") as run:
            reveal_in_finder(path, "/usr/bin/open")
        self.assertEqual(run.call_args.args[0], ["/usr/bin/open", "-R", str(path)])
        self.assertNotIn("input", run.call_args.kwargs)
        self.assertFalse(run.call_args.kwargs.get("shell", False))
        self.assertTrue(run.call_args.kwargs["check"])

    def test_finder_errors_and_timeout_are_reported(self):
        for error, message in (
            (
                subprocess.CalledProcessError(1, "open", stderr="Finder unavailable"),
                "Finder unavailable",
            ),
            (subprocess.TimeoutExpired("open", 15), "timed out"),
        ):
            with (
                self.subTest(error=error),
                patch("b4dcad.preview_finder.subprocess.run", side_effect=error),
                self.assertRaisesRegex(RuntimeError, message),
            ):
                reveal_in_finder(Path("/tmp/model/part.stl"), "/usr/bin/open")

    def test_all_models_share_script_subfolder_even_for_single_model(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = write_finder_exports(
                {"part": b"part mesh", "show_all": b"assembly mesh"},
                directory,
                "wallet.py",
            )
            self.assertEqual(
                paths["part"], Path(directory).resolve() / "wallet" / "part.stl"
            )
            self.assertEqual(paths["part"].read_bytes(), b"part mesh")
            self.assertNotIn("show_all", paths)
            self.assertFalse((paths["part"].parent / "show_all.stl").exists())
            single = write_finder_exports({"only": b"single"}, directory, "single.py")
            self.assertEqual(
                single["only"], Path(directory).resolve() / "single" / "only.stl"
            )

    def test_sanitized_names_do_not_escape_or_overwrite_other_models(self):
        models = {"../part": b"a", "___part": b"b", "___Part": b"c", "模型" * 100: b"d"}
        with tempfile.TemporaryDirectory() as directory:
            paths = write_finder_exports(models, directory, "...py")
            self.assertEqual(
                len({path.name.casefold() for path in paths.values()}), len(models)
            )
            for name, path in paths.items():
                self.assertEqual(path.parent, Path(directory).resolve() / "model")
                self.assertLessEqual(len(path.name.encode("utf-8")), 255)
                self.assertEqual(path.read_bytes(), models[name])

    def test_atomic_export_replaces_symlink_without_writing_its_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "model").mkdir()
            outside = root / "outside"
            outside.write_bytes(b"unchanged")
            (root / "model" / "part.stl").symlink_to(outside)
            path = write_finder_exports({"part": b"new mesh"}, root, "model.py")["part"]
            self.assertEqual(outside.read_bytes(), b"unchanged")
            self.assertFalse(path.is_symlink())
            self.assertEqual(path.read_bytes(), b"new mesh")
            self.assertFalse(list(path.parent.glob(".b4dcad-export-*")))


class ShowInFinderTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.script = self.root / "model.py"
        self.script.write_text(
            "from b4dcad import box\npart = box(1, 2, 3)\nshow_all = part.move(x=3)\n"
        )
        with patch("b4dcad.cli.finder_reveal_command", return_value="/usr/bin/open"):
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

    def test_all_models_are_written_before_revealing_selected_part(self):
        target = self.root / 'quotes "\\\n$(echo unsafe)'
        self.server.write_stl = str(target)

        def run(command, **kwargs):
            path = Path(command[-1])
            self.assertEqual(path, target / "model" / "part.stl")
            for name in self.server.model_names():
                if name.startswith("show"):
                    self.assertFalse((path.parent / f"{name}.stl").exists())
                    continue
                self.assertEqual(
                    (path.parent / f"{name}.stl").read_bytes(),
                    self.server.stl_bytes(name),
                )
            self.assertEqual(command[:2], ["/usr/bin/open", "-R"])
            self.assertNotIn("input", kwargs)

        with patch("b4dcad.preview_finder.subprocess.run", side_effect=run) as finder:
            result = self.server.show_in_finder(**self.request_data())
        finder.assert_called_once()
        self.assertEqual(Path(result["path"]), target / "model" / "part.stl")

    def test_fallback_directory_excludes_preview_only_models(self):
        self.assertEqual(DEFAULT_FINDER_DIRECTORY, "/tmp/stl")
        target = self.root / "fallback"
        with (
            patch("b4dcad.cli.DEFAULT_FINDER_DIRECTORY", str(target)),
            patch("b4dcad.cli.reveal_in_finder") as finder,
        ):
            result = self.server.show_in_finder(**self.request_data())
        path = Path(result["path"])
        self.assertEqual(path, target / "model" / "part.stl")
        self.assertEqual(path.read_bytes(), self.server.stl_bytes("part"))
        self.assertFalse((path.parent / "show_all.stl").exists())
        finder.assert_called_once_with(path, "/usr/bin/open")

    def test_stale_or_unknown_part_is_rejected_before_export(self):
        valid = self.request_data()
        for changes in (
            {"name": "missing"},
            {"name": "show_all"},
            {"name": []},
            {"source": "other.py"},
            {"tree_version": valid["tree_version"] + 1},
        ):
            with (
                self.subTest(changes=changes),
                patch("b4dcad.cli.write_finder_exports") as write,
                patch("b4dcad.cli.reveal_in_finder") as finder,
                self.assertRaises(ValueError),
            ):
                self.server.show_in_finder(**(valid | changes))
            write.assert_not_called()
            finder.assert_not_called()

    def test_export_failure_prevents_finder_open(self):
        with (
            patch("b4dcad.cli.write_finder_exports", side_effect=OSError("disk full")),
            patch("b4dcad.cli.reveal_in_finder") as finder,
            self.assertRaisesRegex(OSError, "disk full"),
        ):
            self.server.show_in_finder(**self.request_data())
        finder.assert_not_called()

    def test_unsupported_backend_hides_capability_and_rejects_reveal(self):
        self.server._finder_command = None
        self.assertFalse(self.server.state()["can_show_in_finder"])
        with (
            patch("b4dcad.cli.write_finder_exports") as write,
            self.assertRaises(NotImplementedError),
        ):
            self.server.show_in_finder(**self.request_data())
        write.assert_not_called()

    def test_http_capability_reveal_and_failure_responses(self):
        self.server.write_stl = str(self.root / "output")
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{self.server.server_port}"

        def post():
            return urlopen(
                Request(
                    base + "/show-in-finder",
                    json.dumps(self.request_data()).encode(),
                    {"Content-Type": "application/json"},
                ),
                timeout=3,
            )

        try:
            with urlopen(base + "/state.json", timeout=3) as response:
                self.assertTrue(json.load(response)["can_show_in_finder"])
            with patch("b4dcad.cli.reveal_in_finder") as finder:
                with post() as response:
                    path = Path(json.load(response)["path"])
                self.assertEqual(path.read_bytes(), self.server.stl_bytes("part"))
                self.assertFalse((path.parent / "show_all.stl").exists())
                finder.assert_called_once()
            with patch(
                "b4dcad.cli.reveal_in_finder",
                side_effect=RuntimeError("Finder unavailable"),
            ):
                with self.assertRaises(HTTPError) as caught:
                    post()
                self.assertEqual(caught.exception.code, 500)
                self.assertIn(
                    "Finder unavailable", json.load(caught.exception)["error"]
                )
                caught.exception.close()
            self.server._finder_command = None
            with self.assertRaises(HTTPError) as caught:
                post()
            self.assertEqual(caught.exception.code, 501)
            caught.exception.close()
        finally:
            self.server.shutdown()
            thread.join()


if __name__ == "__main__":
    unittest.main()
