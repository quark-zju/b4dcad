import argparse
import json
import runpy
import threading
import time
from collections import OrderedDict
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from .core import Shape, Solid

PREVIEW_HTML = Path(__file__).with_name("preview.html")


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


def _preview_sort_key(item):
    name, _model = item
    return (0 if name.startswith("show") else 1, name)


def preview_models(script, name=None):
    models = load_models(script, name)
    return OrderedDict(sorted(models.items(), key=_preview_sort_key))


def export_models(script, name=None):
    models = load_models(script, name)
    return OrderedDict(
        (model_name, model)
        for model_name, model in models.items()
        if not model_name.startswith("show")
    )


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
    for model_name, model in export_models(script, name).items():
        path = stl_path(script, directory, model_name)
        model.stl(path)
        paths[model_name] = path
        print(f"Wrote {path}")
    return paths


class PreviewServer(ThreadingHTTPServer):
    def __init__(
        self,
        address,
        handler,
        script,
        object_name=None,
        write_stl=None,
        poll_interval=1.0,
    ):
        super().__init__(address, handler)
        self.script = script
        self.object_name = object_name
        self.write_stl = write_stl
        self.poll_interval = poll_interval
        self.script_mtime_ns = Path(script).stat().st_mtime_ns
        self.version = self.script_mtime_ns
        self._change = threading.Condition()
        self._stop_watcher = threading.Event()
        if write_stl:
            export_stls(script, write_stl, object_name)
        self._watcher = threading.Thread(target=self._watch, daemon=True)
        self._watcher.start()

    def _watch(self):
        while not self._stop_watcher.wait(self.poll_interval):
            self.check_for_change()

    def check_for_change(self):
        try:
            mtime = Path(self.script).stat().st_mtime_ns
        except OSError as error:
            print(f"Failed to stat {self.script}: {error}", flush=True)
            return False
        if mtime == self.script_mtime_ns:
            return False
        self.script_mtime_ns = mtime
        self.version = mtime
        print(f"Detected change in {self.script}", flush=True)
        if self.write_stl:
            export_stls(self.script, self.write_stl, self.object_name)
        with self._change:
            self._change.notify_all()
        return True

    def wait_for_change(self, version):
        with self._change:
            self._change.wait_for(
                lambda: self.version != version or self._stop_watcher.is_set()
            )
            return self.version

    def server_close(self):
        self._stop_watcher.set()
        with self._change:
            self._change.notify_all()
        super().server_close()


class PreviewHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self._send_html()
            return
        if path == "/events":
            self._send_events()
            return
        if path == "/state.json":
            self._send_json({"version": self.server.version})
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
        source_name = Path(self.server.script).name
        html = PREVIEW_HTML.read_text()
        html = html.replace("__SOURCE_NAME__", source_name)
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
        return preview_models(self.server.script, self.server.object_name)

    def _send_models(self):
        self._send_json(list(self._available_models().keys()))

    def _send_events(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        version = self.server.version
        try:
            self.wfile.write(f": connected {version}\n\n".encode("utf-8"))
            self.wfile.flush()
            while True:
                version = self.server.wait_for_change(version)
                if self.server._stop_watcher.is_set():
                    return
                data = json.dumps({"version": version})
                self.wfile.write(f"event: change\ndata: {data}\n\n".encode("utf-8"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            return

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
