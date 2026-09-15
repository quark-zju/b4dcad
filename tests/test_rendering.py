import importlib.util
import json
import runpy
import sys
import tempfile
import unittest
from pathlib import Path

import b4dcad
from b4dcad.cli import export_models, preview_models


class IsRenderingTest(unittest.TestCase):
    def test_only_main_model_script_is_rendering(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            helper = root / "rendering_helper.py"
            helper.write_text(
                "import b4dcad\n" "def check():\n" "    return b4dcad.is_rendering()\n"
            )
            result = root / "result.json"
            script = root / "model.py"
            script.write_text(
                "import json\n"
                "import b4dcad\n"
                "import rendering_helper\n"
                "def check():\n"
                "    return b4dcad.is_rendering()\n"
                f"with open({str(result)!r}, 'w') as output:\n"
                "    json.dump([b4dcad.is_rendering(), check(), "
                "rendering_helper.check()], output)\n"
                "model = b4dcad.box(1, 1, 1)\n"
            )

            self.assertFalse(b4dcad.is_rendering())
            sys.path.insert(0, str(root))
            try:
                for load in (preview_models, export_models):
                    self.assertEqual(list(load(str(script))), ["model"])
                    self.assertEqual(
                        json.loads(result.read_text()), [True, True, False]
                    )

                runpy.run_path(str(script))
                self.assertEqual(json.loads(result.read_text()), [False, False, False])

                spec = importlib.util.spec_from_file_location("imported_model", script)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                self.assertEqual(json.loads(result.read_text()), [False, False, False])
            finally:
                sys.path.remove(str(root))
                sys.modules.pop("rendering_helper", None)


if __name__ == "__main__":
    unittest.main()
