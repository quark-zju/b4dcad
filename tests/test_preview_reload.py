import os
import runpy
import sys
import tempfile
import unittest
from pathlib import Path

from b4dcad.cli import (
    _LocalModuleTracker,
    PreviewHandler,
    PreviewServer,
    _file_signature,
)


class PreviewReloadTest(unittest.TestCase):
    def test_same_directory_import_is_reloaded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            helper = root / "preview_reload_sibling.py"
            helper.write_text("VALUE = 1\n")
            script = root / "model.py"
            script.write_text(
                "from preview_reload_sibling import VALUE\nresult = VALUE\n"
            )
            tracker = _LocalModuleTracker(script)

            def execute(run_name):
                return runpy.run_path(str(script), run_name=run_name)["result"]

            try:
                self.assertEqual(tracker.execute(execute), 1)
                self.assertEqual(tracker.dependency_paths(), {helper.resolve()})

                old_mtime = helper.stat().st_mtime_ns
                helper.write_text("VALUE = 2\n")
                os.utime(helper, ns=(old_mtime + 1,) * 2)

                self.assertEqual(tracker.execute(execute), 2)
            finally:
                tracker._purge()
                sys.modules.pop("preview_reload_sibling", None)

    def test_relative_transitive_dependency_change_reloads_modules(self):
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / "preview_reload_fixture"
            package.mkdir()
            (package / "__init__.py").write_text("")
            values = package / "values.py"
            values.write_text("SIZE = 1\n")
            (package / "dimensions.py").write_text(
                "from .values import SIZE\nsize = SIZE\n"
            )
            script = package / "model.py"
            script.write_text(
                "from b4dcad import box\n"
                "from .dimensions import size\n"
                "model = box(size, 1, 1)\n"
            )

            server = PreviewServer(
                ("127.0.0.1", 0),
                PreviewHandler,
                str(script),
                poll_interval=3600,
            )
            try:
                original = server.stl_bytes("model")
                self.assertIn(values.resolve(), server._watched_paths())
                self.assertFalse(server.check_for_change())

                old_mtime = values.stat().st_mtime_ns
                values.write_text("SIZE = 2\n")
                os.utime(values, ns=(old_mtime + 1_000_000_000,) * 2)

                self.assertTrue(server.check_for_change())
                self.assertNotEqual(server.stl_bytes("model"), original)
                self.assertFalse(server.check_for_change())
            finally:
                server.server_close()
                server._module_tracker._purge()
                for name in list(sys.modules):
                    if name == "preview_reload_fixture" or name.startswith(
                        "preview_reload_fixture."
                    ):
                        sys.modules.pop(name, None)

    def test_file_signature_tracks_replacement_and_missing_files(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "dependency.py"
            replacement = Path(directory) / "replacement.py"
            path.write_text("VALUE = 1\n")
            original = _file_signature(path)

            replacement.write_text("VALUE = 1\n")
            os.utime(replacement, ns=(original[0], original[0]))
            os.replace(replacement, path)

            replaced = _file_signature(path)
            self.assertEqual(replaced[:2], original[:2])
            self.assertNotEqual(replaced[2], original[2])

            path.unlink()
            self.assertIsNone(_file_signature(path))


if __name__ == "__main__":
    unittest.main()
