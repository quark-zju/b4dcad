import unittest

from b4dcad.cli import load_preview_html


class PreviewHtmlTest(unittest.TestCase):
    def test_dimensions_overlay_uses_loaded_geometry_bounding_box(self):
        html = load_preview_html()

        self.assertIn('id="dimensions"', html)
        self.assertIn("function updateDimensions(box)", html)
        self.assertIn("updateDimensions(box);", html)
        self.assertIn("right: 12px;", html)
        self.assertIn("bottom: 12px;", html)
        self.assertIn("× Z ${formatDimension(size.z)} mm", html)


if __name__ == "__main__":
    unittest.main()
