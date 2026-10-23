import importlib.util
from pathlib import Path
import tempfile
import unittest

from b4dcad import cube


@unittest.skipUnless(
    importlib.util.find_spec("matplotlib"), "optional matplotlib is not installed"
)
class PngTest(unittest.TestCase):
    def test_headless_render(self):
        from PIL import Image

        part = cube(2, 3, 4)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "part.png"
            self.assertIs(part.png(path, size=160, max_edge=0), part)
            with Image.open(path) as image:
                self.assertEqual(image.format, "PNG")
                self.assertGreater(image.width, 100)
                self.assertGreater(
                    len(image.convert("RGB").getcolors(image.width * image.height)), 2
                )
