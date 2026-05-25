import argparse
import runpy
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .badcad import Shape, Solid


DEFAULT_NAMES = ("model", "solid", "part", "shape")


def load_model(script, name=None):
    namespace = runpy.run_path(script)
    if name:
        if name not in namespace:
            raise ValueError(f"{script} does not define {name!r}")
        model = namespace[name]
    else:
        model = next((namespace[n] for n in DEFAULT_NAMES if n in namespace), None)
        if model is None and "build" in namespace:
            model = namespace["build"]
        if model is None:
            names = ", ".join(DEFAULT_NAMES + ("build",))
            raise ValueError(f"{script} must define one of: {names}")

    if callable(model):
        model = model()
    if isinstance(model, Shape):
        raise TypeError("STL export requires a Solid; extrude the Shape before exporting")
    if not isinstance(model, Solid):
        raise TypeError(f"expected badcad Solid, got {type(model).__name__}")
    return model


def export_stl(script, output, name=None):
    model = load_model(script, name)
    model.stl(output)
    return model


class PreviewHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, script=None, object_name=None, **kwargs):
        self.script = script
        self.object_name = object_name
        super().__init__(*args, **kwargs)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._send_html()
            return
        if self.path.startswith("/model.stl"):
            self._send_stl()
            return
        self.send_error(404)

    def _send_html(self):
        html = """<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>badcad preview</title>
  <style>
    html, body { margin: 0; height: 100%; overflow: hidden; background: #f4f4f0; }
    canvas { display: block; }
    #bar {
      position: fixed; left: 12px; top: 12px; z-index: 1;
      font: 13px system-ui, sans-serif; color: #222;
      background: rgba(255,255,255,.82); padding: 8px 10px; border: 1px solid #ddd;
    }
  </style>
</head>
<body>
  <div id="bar">badcad preview</div>
  <script type="module">
    import * as THREE from "https://cdn.jsdelivr.net/npm/three@0.164.1/build/three.module.js";
    import { OrbitControls } from "https://cdn.jsdelivr.net/npm/three@0.164.1/examples/jsm/controls/OrbitControls.js";
    import { STLLoader } from "https://cdn.jsdelivr.net/npm/three@0.164.1/examples/jsm/loaders/STLLoader.js";

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0xf4f4f0);
    const camera = new THREE.PerspectiveCamera(45, innerWidth / innerHeight, 0.01, 10000);
    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setSize(innerWidth, innerHeight);
    renderer.setPixelRatio(devicePixelRatio);
    document.body.appendChild(renderer.domElement);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    scene.add(new THREE.HemisphereLight(0xffffff, 0x606060, 2.4));
    const key = new THREE.DirectionalLight(0xffffff, 1.5);
    key.position.set(1, -2, 3);
    scene.add(key);

    const loader = new STLLoader();
    loader.load(`/model.stl?ts=${Date.now()}`, (geometry) => {
      geometry.computeVertexNormals();
      geometry.center();
      const material = new THREE.MeshStandardMaterial({ color: 0xb8aa2e, roughness: 0.48, metalness: 0.05 });
      const mesh = new THREE.Mesh(geometry, material);
      scene.add(mesh);

      const box = new THREE.Box3().setFromObject(mesh);
      const size = box.getSize(new THREE.Vector3()).length();
      camera.position.set(size * 0.55, size * -1.15, size * 0.75);
      camera.near = Math.max(size / 1000, 0.01);
      camera.far = Math.max(size * 100, 100);
      camera.updateProjectionMatrix();
      controls.update();
    });

    addEventListener("resize", () => {
      camera.aspect = innerWidth / innerHeight;
      camera.updateProjectionMatrix();
      renderer.setSize(innerWidth, innerHeight);
    });

    function animate() {
      requestAnimationFrame(animate);
      controls.update();
      renderer.render(scene, camera);
    }
    animate();
  </script>
</body>
</html>
"""
        data = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_stl(self):
        model = load_model(self.script, self.object_name)
        data = model.stl()
        self.send_response(200)
        self.send_header("Content-Type", "model/stl")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def stl_command(argv=None):
    parser = argparse.ArgumentParser(description="Export a badcad Python model to STL.")
    parser.add_argument(
        "script",
        help="Python script defining model, solid, part, shape, or build()",
    )
    parser.add_argument("output", help="Output STL path")
    parser.add_argument("--object", dest="object_name", help="Object or function name to export")
    args = parser.parse_args(argv)
    export_stl(args.script, args.output, args.object_name)


def preview_command(argv=None):
    parser = argparse.ArgumentParser(
        description="Serve a browser STL preview for a badcad Python model."
    )
    parser.add_argument(
        "script",
        help="Python script defining model, solid, part, shape, or build()",
    )
    parser.add_argument("--object", dest="object_name", help="Object or function name to preview")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)

    script = str(Path(args.script).resolve())
    handler = partial(PreviewHandler, script=script, object_name=args.object_name)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"Serving {script} at http://{args.host}:{args.port}")
    server.serve_forever()


def main(argv=None):
    parser = argparse.ArgumentParser(prog="badcad")
    subparsers = parser.add_subparsers(dest="command", required=True)

    stl_parser = subparsers.add_parser("stl", help="Export a model script to STL")
    stl_parser.add_argument("script")
    stl_parser.add_argument("output")
    stl_parser.add_argument("--object", dest="object_name")

    preview_parser = subparsers.add_parser("preview", help="Serve a browser preview")
    preview_parser.add_argument("script")
    preview_parser.add_argument("--object", dest="object_name")
    preview_parser.add_argument("--host", default="127.0.0.1")
    preview_parser.add_argument("--port", type=int, default=8765)

    args = parser.parse_args(argv)
    if args.command == "stl":
        export_stl(args.script, args.output, args.object_name)
    elif args.command == "preview":
        script = str(Path(args.script).resolve())
        handler = partial(PreviewHandler, script=script, object_name=args.object_name)
        server = ThreadingHTTPServer((args.host, args.port), handler)
        print(f"Serving {script} at http://{args.host}:{args.port}")
        server.serve_forever()


if __name__ == "__main__":
    main()
