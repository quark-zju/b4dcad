import unittest

from b4dcad.cli import load_preview_favicon, load_preview_html


class PreviewHtmlTest(unittest.TestCase):
    def test_favicon_supports_light_and_dark_color_schemes(self):
        html = load_preview_html()
        favicon = load_preview_favicon().decode("utf-8")

        self.assertIn('rel="icon"', html)
        self.assertIn('href="/favicon.svg"', html)
        self.assertIn("prefers-color-scheme: dark", favicon)
        self.assertIn('viewBox="0 0 64 64"', favicon)

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
