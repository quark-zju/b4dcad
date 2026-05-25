import argparse
import json
import runpy
from collections import OrderedDict
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from .core import Shape, Solid


def _require_solid(value, name):
    if callable(value):
        value = value()
    if isinstance(value, Shape):
        raise TypeError(f"{name} is a Shape; STL export requires a Solid")
    if not isinstance(value, Solid):
        raise TypeError(f"{name} must be a b4dcad Solid, got {type(value).__name__}")
    return value


def load_models(script, name=None):
    namespace = runpy.run_path(script)
    if name:
        if name not in namespace:
            raise ValueError(f"{script} does not define {name!r}")
        return OrderedDict([(name, _require_solid(namespace[name], name))])

    models = OrderedDict(
        (key, value)
        for key, value in namespace.items()
        if not key.startswith("_") and isinstance(value, Solid)
    )
    if models:
        return models

    raise ValueError(f"{script} does not define any public b4dcad Solid variables")


def load_model(script, name=None):
    if name is not None:
        return next(iter(load_models(script, name).values()))
    return next(iter(load_models(script).values()))


def stl_path(script, directory, name):
    stem = Path(script).stem
    return Path(directory) / f"{stem}-{name}.stl"


def export_stls(script, directory, name=None):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    paths = OrderedDict()
    for model_name, model in load_models(script, name).items():
        path = stl_path(script, directory, model_name)
        model.stl(path)
        paths[model_name] = path
        print(f"Wrote {path}")
    return paths


class PreviewServer(ThreadingHTTPServer):
    def __init__(self, address, handler, script, object_name=None, write_stl=None):
        super().__init__(address, handler)
        self.script = script
        self.object_name = object_name
        self.write_stl = write_stl
        self.script_mtime_ns = Path(script).stat().st_mtime_ns
        self.version = self.script_mtime_ns
        if write_stl:
            export_stls(script, write_stl, object_name)

    def check_for_change(self):
        mtime = Path(self.script).stat().st_mtime_ns
        if mtime == self.script_mtime_ns:
            return False
        self.script_mtime_ns = mtime
        self.version = mtime
        print(f"Detected change in {self.script}", flush=True)
        if self.write_stl:
            export_stls(self.script, self.write_stl, self.object_name)
        return True


class PreviewHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self._send_html()
            return
        if path == "/state.json":
            self._send_state()
            return
        if path == "/models.json":
            self._send_models()
            return
        if path == "/model.stl":
            self._send_stl(None)
            return
        if path.startswith("/model/") and path.endswith(".stl"):
            name = unquote(path[len("/model/") : -len(".stl")])
            self._send_stl(name)
            return
        self.send_error(404)

    def _send_html(self):
        html = """<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>b4dcad preview</title>
  <style>
    html, body { margin: 0; height: 100%; overflow: hidden; background: #f4f4f0; }
    canvas { display: block; }
    #bar {
      position: fixed; left: 0; right: 0; top: 0; z-index: 1;
      display: flex; gap: 8px; align-items: center; flex-wrap: wrap;
      font: 13px system-ui, sans-serif; color: #222;
      background: rgba(255,255,255,.88); padding: 10px 12px; border-bottom: 1px solid #ddd;
    }
    #bar button {
      appearance: none; border: 1px solid #ccc; background: #fff; color: #222;
      padding: 6px 10px; cursor: pointer;
    }
    #bar button[aria-pressed="true"] {
      background: #222; color: #fff; border-color: #222;
    }
    #bar .error {
      color: #8a1f11;
    }
  </style>
  <script type="importmap">
    {
      "imports": {
        "three": "https://cdn.jsdelivr.net/npm/three@0.164.1/build/three.module.js",
        "three/addons/": "https://cdn.jsdelivr.net/npm/three@0.164.1/examples/jsm/"
      }
    }
  </script>
</head>
<body>
  <nav id="bar" aria-label="Components"></nav>
  <script type="module">
    import * as THREE from "three";
    import { OrbitControls } from "three/addons/controls/OrbitControls.js";
    import { STLLoader } from "three/addons/loaders/STLLoader.js";

    const bar = document.querySelector("#bar");
    const showError = (message) => {
      const item = document.createElement("span");
      item.className = "error";
      item.textContent = message;
      bar.append(item);
    };

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
    let mesh = null;
    let activeName = null;

    function setActiveButton(name) {
      for (const button of bar.querySelectorAll("button")) {
        button.setAttribute("aria-pressed", String(button.dataset.name === name));
      }
    }

    function loadModel(name) {
      activeName = name;
      setActiveButton(name);
      const url = `/model/${encodeURIComponent(name)}.stl?ts=${Date.now()}`;
      loader.load(url, (geometry) => {
      geometry.computeVertexNormals();
      geometry.center();
      const material = new THREE.MeshStandardMaterial({ color: 0xb8aa2e, roughness: 0.48, metalness: 0.05 });
      if (mesh) {
        scene.remove(mesh);
        mesh.geometry.dispose();
        mesh.material.dispose();
      }
      mesh = new THREE.Mesh(geometry, material);
      scene.add(mesh);

      const box = new THREE.Box3().setFromObject(mesh);
      const size = box.getSize(new THREE.Vector3()).length();
      camera.position.set(size * 0.55, size * -1.15, size * 0.75);
      camera.near = Math.max(size / 1000, 0.01);
      camera.far = Math.max(size * 100, 100);
      camera.updateProjectionMatrix();
      controls.update();
      }, undefined, (error) => showError(`Failed to load ${name}: ${error.message || error}`));
    }

    async function loadModelList() {
      const response = await fetch(`/models.json?ts=${Date.now()}`);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const models = await response.json();
      bar.textContent = "";
      for (const name of models) {
        const button = document.createElement("button");
        button.type = "button";
        button.dataset.name = name;
        button.textContent = name;
        button.addEventListener("click", () => loadModel(name));
        bar.append(button);
      }
      if (models.length === 0) {
        showError("No public b4dcad Solid variables found");
        return;
      }
      loadModel(models.includes(activeName) ? activeName : models[0]);
    }

    loadModelList().catch((error) => showError(`Failed to load models: ${error.message || error}`));

    let version = null;
    async function pollState() {
      try {
        const response = await fetch(`/state.json?ts=${Date.now()}`);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const state = await response.json();
        if (version === null) {
          version = state.version;
        } else if (version !== state.version) {
          location.reload();
        }
      } catch (error) {
        showError(`Live preview disconnected: ${error.message || error}`);
      }
    }
    pollState();
    setInterval(pollState, 1000);

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

    def _send_json(self, value):
        data = json.dumps(value).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _available_models(self):
        return load_models(self.server.script, self.server.object_name)

    def _send_state(self):
        self.server.check_for_change()
        self._send_json({"version": self.server.version})

    def _send_models(self):
        self._send_json(list(self._available_models().keys()))

    def _send_stl(self, name):
        models = self._available_models()
        if name is None:
            model = next(iter(models.values()))
        else:
            if name not in models:
                self.send_error(404, f"Unknown model {name!r}")
                return
            model = models[name]
        data = model.stl()
        self.send_response(200)
        self.send_header("Content-Type", "model/stl")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def stl_command(argv=None):
    parser = argparse.ArgumentParser(description="Export a b4dcad Python model to STL.")
    parser.add_argument(
        "script",
        help="Python script defining public b4dcad Solid variables",
    )
    parser.add_argument("directory", help="Output directory")
    parser.add_argument(
        "--object", dest="object_name", help="Object or function name to export"
    )
    args = parser.parse_args(argv)
    export_stls(args.script, args.directory, args.object_name)


def preview_command(argv=None):
    parser = argparse.ArgumentParser(
        description="Serve a browser STL preview for a b4dcad Python model."
    )
    parser.add_argument(
        "script",
        help="Python script defining model, solid, part, shape, or build()",
    )
    parser.add_argument(
        "--object", dest="object_name", help="Object or function name to preview"
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--write-stl", dest="write_stl", help="Directory to write STL files"
    )
    args = parser.parse_args(argv)

    script = str(Path(args.script).resolve())
    server = PreviewServer(
        (args.host, args.port),
        PreviewHandler,
        script,
        object_name=args.object_name,
        write_stl=args.write_stl,
    )
    print(f"Serving {script} at http://{args.host}:{args.port}")
    server.serve_forever()


def main(argv=None):
    parser = argparse.ArgumentParser(prog="b4dcad")
    subparsers = parser.add_subparsers(dest="command", required=True)

    stl_parser = subparsers.add_parser("stl", help="Export a model script to STL")
    stl_parser.add_argument("script")
    stl_parser.add_argument("directory")
    stl_parser.add_argument("--object", dest="object_name")

    preview_parser = subparsers.add_parser("preview", help="Serve a browser preview")
    preview_parser.add_argument("script")
    preview_parser.add_argument("--object", dest="object_name")
    preview_parser.add_argument("--host", default="127.0.0.1")
    preview_parser.add_argument("--port", type=int, default=8765)
    preview_parser.add_argument("--write-stl", dest="write_stl")

    args = parser.parse_args(argv)
    if args.command == "stl":
        export_stls(args.script, args.directory, args.object_name)
    elif args.command == "preview":
        script = str(Path(args.script).resolve())
        server = PreviewServer(
            (args.host, args.port),
            PreviewHandler,
            script,
            object_name=args.object_name,
            write_stl=args.write_stl,
        )
        print(f"Serving {script} at http://{args.host}:{args.port}")
        server.serve_forever()


if __name__ == "__main__":
    main()
