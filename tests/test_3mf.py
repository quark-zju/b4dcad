import io
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile

import numpy as np
from b4dcad import Solid, cube, save_3mf


class ThreeMFTest(unittest.TestCase):
    def test_objects_colors_names_and_precision(self):
        part = cube(0.1234567890123, 2, 3).move(x=123456.7890123)
        binary = save_3mf(
            None, [('A & "B"', part, (0.5, 0.0, 1.0)), Solid(), ("other", cube())]
        )
        with zipfile.ZipFile(io.BytesIO(binary)) as archive:
            self.assertEqual(
                set(archive.namelist()),
                {"[Content_Types].xml", "_rels/.rels", "3D/3dmodel.model"},
            )
            root = ET.fromstring(archive.read("3D/3dmodel.model"))
        ns = {"m": "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"}
        objects = root.findall("m:resources/m:object", ns)
        self.assertEqual([o.attrib["name"] for o in objects], ['A & "B"', "other"])
        self.assertEqual(len(root.findall("m:build/m:item", ns)), 2)
        self.assertEqual(
            root.find("m:resources/m:basematerials/m:base", ns).attrib["displaycolor"],
            "#8000FF",
        )
        actual = [
            [float(v.attrib[a]) for a in "xyz"]
            for v in objects[0].findall("m:mesh/m:vertices/m:vertex", ns)
        ]
        np.testing.assert_array_equal(
            actual, part.manifold.to_mesh64().vert_properties[:, :3]
        )

    def test_file_and_single_solid(self):
        part = cube()
        self.assertIsInstance(part.threemf(), bytes)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "part.3mf"
            self.assertIs(part.threemf(path), part)
            self.assertTrue(zipfile.is_zipfile(path))
        for color in ("#GG0011", (1.0, 0.0), (float("nan"), 0.0, 0.0)):
            with self.assertRaises(ValueError):
                part.threemf(color=color)
