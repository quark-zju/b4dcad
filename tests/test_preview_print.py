import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import urlopen

from b4dcad import box
from b4dcad.cli import PreviewHandler, PreviewServer, _estimate_preview_material
from b4dcad.preview_print import print_material_volume


class PrintMaterialTest(unittest.TestCase):
    def test_rotated_box_keeps_wall_thickness(self):
        part = box(20, 20, 20).rotate(z=45).move(z=-35)
        self.assertAlmostEqual(
            print_material_volume(part.manifold), 20**3 - 0.85 * 18.4**3, places=4
        )

    def test_step_creates_local_top_layers(self):
        part = box(20, 20, 3) + box(10, 10, 2).move(x=5, y=5, z=3)
        interior = 18.4**2 * 1.4 + 10**2 * 1.0 + 8.4**2 * 1.0
        self.assertAlmostEqual(
            print_material_volume(part.manifold), 1400 - 0.85 * interior, places=4
        )

    def test_partial_layer_and_wall_only(self):
        part = box(1, 5, 2.05)
        self.assertAlmostEqual(print_material_volume(part.manifold), 10.25)

    def test_preview_api_reuses_tree_estimate_and_invalidates_on_rebuild(self):
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "model.py"
            script.write_text("from b4dcad import box\npart = box(20, 20, 20)\n")
            with PreviewServer(
                ("127.0.0.1", 0), PreviewHandler, script, poll_interval=60
            ) as server:
                worker = threading.Thread(
                    target=server.serve_forever, kwargs={"poll_interval": 0.01}
                )
                worker.start()
                try:
                    base = f"http://127.0.0.1:{server.server_port}/print-estimate/"
                    state = server.state()
                    version = state["tree_version"]
                    node = state["tree"]["roots"]["part"]

                    def get(path):
                        with urlopen(base + path, timeout=3) as response:
                            return json.load(response)

                    with patch(
                        "b4dcad.cli._estimate_preview_material",
                        wraps=_estimate_preview_material,
                    ) as estimate:
                        result = get(f"{version}/model/part.json")
                        self.assertAlmostEqual(result["pla_g"], 3.354102784)
                        self.assertAlmostEqual(result["petg_g"], 3.435250432)
                        self.assertEqual(get(f"{version}/tree/{node}.json"), result)
                        estimate.assert_called_once()

                    script.write_text("from b4dcad import box\npart = box(1, 1, 1)\n")
                    server.rebuild_cache()
                    with self.assertRaises(HTTPError) as stale:
                        get(f"{version}/model/part.json")
                    self.assertEqual(stale.exception.code, 404)
                    result = get(f"{server.state()['tree_version']}/model/part.json")
                    self.assertAlmostEqual(result["pla_g"], 0.00124)
                finally:
                    server.shutdown()
                    worker.join()
