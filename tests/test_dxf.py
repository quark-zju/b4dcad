import unittest

from b4dcad import dxf


def drawing(*entities):
    return "\n".join(
        ("0", "SECTION", "2", "ENTITIES", *entities, "0", "ENDSEC", "0", "EOF")
    )


class DxfTest(unittest.TestCase):
    def test_circle_and_polyline_hole(self):
        data = drawing(
            "0",
            "LWPOLYLINE",
            "70",
            "1",
            "10",
            "-2",
            "20",
            "-2",
            "10",
            "2",
            "20",
            "-2",
            "10",
            "2",
            "20",
            "2",
            "10",
            "-2",
            "20",
            "2",
            "0",
            "CIRCLE",
            "10",
            "0",
            "20",
            "0",
            "40",
            "1",
        )
        import math

        self.assertAlmostEqual(dxf(data=data, fn=256).area(), 16 - math.pi, delta=0.001)

    def test_unknown_entities_require_handler(self):
        data = drawing("0", "TEXT", "1", "hello")
        with self.assertRaises(ValueError):
            dxf(data=data)
        seen = []

        def handle(kind, codes):
            seen.append((kind, codes))
            return []

        self.assertTrue(dxf(data=data, on_unknown=handle).is_empty())
        self.assertEqual(seen, [("TEXT", [("1", "hello")])])
