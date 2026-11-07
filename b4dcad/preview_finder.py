"""Export preview models into a script folder and reveal a file in Finder."""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

DEFAULT_FINDER_DIRECTORY = "/tmp/stl"


def finder_reveal_command():
    command = "/usr/bin/open"
    if sys.platform == "darwin" and os.access(command, os.X_OK):
        return command
    return None


def _filename_part(value):
    value = "".join(char if char.isalnum() or char in "-_." else "_" for char in value)
    # Leave room for the extension and disambiguating suffixes in a 255-byte name.
    return value.strip(".").encode("utf-8")[:96].decode("utf-8", "ignore") or "model"


def write_finder_exports(models, directory, script):
    directory = Path(directory).expanduser().resolve() / _filename_part(
        Path(script).stem
    )
    directory.mkdir(parents=True, exist_ok=True)
    paths, used = {}, set()
    for name, data in models.items():
        if name.startswith("show"):
            continue
        stem = _filename_part(name)
        filename = f"{stem}.stl"
        suffix = 1
        # Finder volumes commonly use case-insensitive filenames. Keep every model.
        while filename.casefold() in used:
            suffix += 1
            filename = f"{stem}-{suffix}.stl"
        used.add(filename.casefold())
        path = directory / filename
        _write_atomic(data, path)
        paths[name] = path
    return paths


def _write_atomic(data, path):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent, prefix=".b4dcad-export-", suffix=".stl", delete=False
        ) as output:
            temporary = Path(output.name)
            output.write(data)
        # Replace atomically, including any preexisting symlink at the output path.
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def reveal_in_finder(path, command):
    try:
        subprocess.run(
            [command, "-R", str(path)],
            text=True,
            capture_output=True,
            check=True,
            timeout=15,
        )
    except subprocess.CalledProcessError as error:
        raise RuntimeError(
            (error.stderr or "").strip() or "Could not reveal the model in Finder"
        ) from error
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("Finder reveal timed out") from error
