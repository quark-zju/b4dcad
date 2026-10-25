import tempfile
import unittest
from pathlib import Path

from b4dcad.cli import build_preview_cache, export_stls, write_cached_stls


class StlExportTest(unittest.TestCase):
    def test_export_paths_and_contents(self):
        for count, selected in ((0, None), (1, None), (2, None), (2, "part0")):
            with self.subTest(count=count, selected=selected):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    script = root / "example.py"
                    script.write_text(
                        "from b4dcad import box\nshow_all = box(5, 5, 5)\n"
                        + "".join(
                            f"part{i} = box({i + 1}, 2, 3)\n" for i in range(count)
                        )
                    )
                    direct = root / "direct"
                    preview = root / "preview"
                    paths = export_stls(str(script), direct, selected)
                    cached_paths = write_cached_stls(
                        script,
                        preview,
                        build_preview_cache(str(script), selected),
                    )
                    names = (
                        [selected] if selected else [f"part{i}" for i in range(count)]
                    )
                    expected = {
                        (
                            Path("example.stl")
                            if len(names) == 1
                            else Path("example") / f"{name}.stl"
                        )
                        for name in names
                    }
                    self.assertEqual(set(paths), set(names))
                    self.assertEqual(set(cached_paths), set(names))
                    for output in (direct, preview):
                        self.assertEqual(
                            {p.relative_to(output) for p in output.rglob("*.stl")},
                            expected,
                        )
                    for path in expected:
                        self.assertTrue((direct / path).read_bytes())
                        self.assertEqual(
                            (direct / path).read_bytes(), (preview / path).read_bytes()
                        )


if __name__ == "__main__":
    unittest.main()
