import argparse
import importlib
import importlib.resources
import importlib.util
import json
import runpy
import sys
import tempfile
import threading
import traceback
from collections import OrderedDict
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from .core import Shape, Solid
from .rendering import _RENDERING_MARKER

PREVIEW_HTML = "preview.html"
CADQUERY_STL_TOLERANCE = 0.05


def load_preview_html():
    local = Path(__file__).with_name(PREVIEW_HTML)
    if local.exists():
        return local.read_text()
    return importlib.resources.files("b4dcad").joinpath(PREVIEW_HTML).read_text()


def _is_cadquery_workplane(value):
    if type(value).__name__ != "Workplane":
        return False
    try:
        import cadquery as cq
    except ImportError as e:
        raise TypeError(
            "object looks like a CadQuery Workplane, but cadquery is not installed"
        ) from e
    return isinstance(value, cq.Workplane)


def is_model(value):
    return isinstance(value, Solid) or _is_cadquery_workplane(value)


def _require_model(value, name):
    if callable(value):
        value = value()
    if isinstance(value, Shape):
        raise TypeError(f"{name} is a Shape; STL export requires a Solid")
    if not is_model(value):
        raise TypeError(
            f"{name} must be a b4dcad Solid or CadQuery Workplane, got {type(value).__name__}"
        )
    return value


def model_stl_bytes(model):
    if isinstance(model, Solid):
        return model.stl()

    if _is_cadquery_workplane(model):
        import cadquery as cq

        tmp = tempfile.NamedTemporaryFile(suffix=".stl", delete=False)
        try:
            tmp.close()
            cq.exporters.export(
                model,
                tmp.name,
                exportType="STL",
                tolerance=CADQUERY_STL_TOLERANCE,
            )
            return Path(tmp.name).read_bytes()
        finally:
            Path(tmp.name).unlink(missing_ok=True)

    raise TypeError(f"unsupported model type {type(model).__name__}")


def write_model_stl(model, path):
    if isinstance(model, Solid):
        return model.stl(path)
    data = model_stl_bytes(model)
    with open(path, "wb") as f:
        f.write(data)
    return model


def load_models(script, name=None, run_name=None):
    namespace = runpy.run_path(
        script,
        init_globals={"__b4dcad_rendering__": _RENDERING_MARKER},
        run_name=run_name,
    )
    if name:
        if name not in namespace:
            raise ValueError(f"{script} does not define {name!r}")
        return OrderedDict([(name, _require_model(namespace[name], name))])

    models = OrderedDict(
        (key, value)
        for key, value in namespace.items()
        if not key.startswith("_") and is_model(value)
    )
    if models:
        return models

    raise ValueError(
        f"{script} does not define any public b4dcad Solid or CadQuery Workplane variables"
    )


def _preview_sort_key(item):
    name, _model = item
    return (0 if name.startswith("show") else 1, name)


def preview_models(script, name=None, run_name=None):
    models = load_models(script, name, run_name=run_name)
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
        write_model_stl(model, path)
        paths[model_name] = path
        print(f"Wrote {path}")
    return paths


def build_preview_cache(script, name=None, run_name=None):
    models = preview_models(script, name, run_name=run_name)
    return OrderedDict(
        (model_name, model_stl_bytes(model)) for model_name, model in models.items()
    )


def write_cached_stls(script, directory, cache):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    paths = OrderedDict()
    for model_name, data in cache.items():
        if model_name.startswith("show"):
            continue
        path = stl_path(script, directory, model_name)
        with open(path, "wb") as f:
            f.write(data)
        paths[model_name] = path
        print(f"Wrote {path}")
    return paths


class _LocalModuleTracker:
    """Discover and reload modules imported from beside a model script."""

    def __init__(self, script):
        self.script = Path(script).resolve()
        self.local_root = self.script.parent
        self.protected_modules = frozenset(sys.modules)
        self.modules = {}
        self.import_root, self.run_name = self._execution_context()

    def _execution_context(self):
        package_parts = []
        directory = self.script.parent
        while (directory / "__init__.py").is_file():
            package_parts.insert(0, directory.name)
            directory = directory.parent

        if not package_parts:
            return self.script.parent, None

        package = ".".join(package_parts)
        return directory, f"{package}.__b4dcad_preview__"

    def execute(self, callback):
        self._purge()
        before = frozenset(sys.modules)
        sys.path.insert(0, str(self.import_root))
        succeeded = False
        try:
            result = callback(self.run_name)
            succeeded = True
            return result
        finally:
            sys.path.pop(0)
            discovered = self._discover(frozenset(sys.modules) - before)
            if succeeded:
                self.modules = discovered
            else:
                # Keep earlier dependencies so fixing a broken file retriggers the build.
                self.modules.update(discovered)

    def _discover(self, names):
        modules = {}
        for name in names:
            if name in self.protected_modules:
                continue
            module = sys.modules.get(name)
            filename = getattr(module, "__file__", None)
            if filename is None:
                continue
            path = Path(filename).resolve()
            if path.suffix in {".pyc", ".pyo"}:
                try:
                    path = Path(importlib.util.source_from_cache(str(path))).resolve()
                except ValueError:
                    continue
            if path == self.local_root or path.is_relative_to(self.local_root):
                modules[name] = path
        return modules

    def _purge(self):
        for name in sorted(
            self.modules, key=lambda value: value.count("."), reverse=True
        ):
            module = sys.modules.pop(name, None)
            if module is None:
                continue

            parent_name, separator, child_name = name.rpartition(".")
            if separator:
                parent = sys.modules.get(parent_name)
                if parent is not None and getattr(parent, child_name, None) is module:
                    delattr(parent, child_name)

            cached = getattr(module, "__cached__", None)
            if cached is not None:
                Path(cached).unlink(missing_ok=True)

        importlib.invalidate_caches()

    def dependency_paths(self):
        return set(self.modules.values())


def _file_signature(path):
    try:
        stat = Path(path).stat()
    except OSError:
        return None
    return (stat.st_mtime_ns, stat.st_size, stat.st_ino)


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
        self._cache_lock = threading.RLock()
        self._stl_cache = OrderedDict()
        self._error = None
        self._change = threading.Condition()
        self._stop_watcher = threading.Event()
        super().__init__(address, handler)
        self.script = script
        self.object_name = object_name
        self.write_stl = write_stl
        self.poll_interval = poll_interval
        self._module_tracker = _LocalModuleTracker(script)
        self.version = 1
        self.rebuild_cache(write_files=bool(write_stl))
        self._file_signatures = self._current_file_signatures()
        self._watcher = threading.Thread(target=self._watch, daemon=True)
        self._watcher.start()

    def rebuild_cache(self, write_files=False):
        try:
            cache = self._module_tracker.execute(
                lambda run_name: build_preview_cache(
                    self.script, self.object_name, run_name=run_name
                )
            )
        except Exception as error:
            preview_error = {
                "message": str(error),
                "traceback": traceback.format_exc(),
            }
            with self._cache_lock:
                self._error = preview_error
            print(preview_error["traceback"], flush=True)
            return None

        with self._cache_lock:
            self._stl_cache = cache
            self._error = None
        if write_files and self.write_stl:
            write_cached_stls(self.script, self.write_stl, cache)
        return cache

    def model_names(self):
        with self._cache_lock:
            return list(self._stl_cache.keys())

    def state(self):
        with self._cache_lock:
            return {
                "version": self.version,
                "models": list(self._stl_cache.keys()),
                "error": self._error,
            }

    def stl_bytes(self, name=None):
        with self._cache_lock:
            if name is None:
                return next(iter(self._stl_cache.values()))
            if name not in self._stl_cache:
                return None
            return self._stl_cache[name]

    def _watch(self):
        while not self._stop_watcher.wait(self.poll_interval):
            self.check_for_change()

    def _watched_paths(self):
        return {Path(self.script), *self._module_tracker.dependency_paths()}

    def _current_file_signatures(self):
        return {path: _file_signature(path) for path in self._watched_paths()}

    def check_for_change(self):
        observed = self._current_file_signatures()
        changed = [
            path
            for path in observed.keys() | self._file_signatures.keys()
            if observed.get(path) != self._file_signatures.get(path)
        ]
        if not changed:
            return False

        # Preserve pre-build signatures for existing dependencies. A file edited
        # during a slow build will then be noticed by the next poll.
        self._file_signatures = observed
        self.version += 1
        print(
            "Detected change in " + ", ".join(str(path) for path in sorted(changed)),
            flush=True,
        )
        self.rebuild_cache(write_files=bool(self.write_stl))
        watched = self._watched_paths()
        self._file_signatures = {
            path: (
                self._file_signatures[path]
                if path in self._file_signatures
                else _file_signature(path)
            )
            for path in watched
        }
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
            self._send_json(self.server.state())
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
        html = load_preview_html()
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

    def _send_models(self):
        self._send_json(self.server.model_names())

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
        data = self.server.stl_bytes(name)
        if data is None:
            self.send_error(404, f"Unknown model {name!r}")
            return
        self.send_response(200)
        self.send_header("Content-Type", "model/stl")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def stl_command(argv=None):
    parser = argparse.ArgumentParser(description="Export a Python CAD model to STL.")
    parser.add_argument(
        "script",
        help="Python script defining public b4dcad Solid or CadQuery Workplane variables",
    )
    parser.add_argument("directory", help="Output directory")
    parser.add_argument(
        "--object", dest="object_name", help="Object or function name to export"
    )
    args = parser.parse_args(argv)
    export_stls(args.script, args.directory, args.object_name)


def preview_command(argv=None):
    parser = argparse.ArgumentParser(
        description="Serve a browser STL preview for a Python CAD model."
    )
    parser.add_argument(
        "script",
        help="Python script defining public b4dcad Solid or CadQuery Workplane variables",
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

    # allow relative import
    sys.path.insert(0, ".")

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
