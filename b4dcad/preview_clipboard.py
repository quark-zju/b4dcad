"""Export preview files and publish Finder-compatible file clipboard data."""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

DEFAULT_COPY_DIRECTORY = "/tmp/stl"
FINDER_COPY_SCRIPT = """use framework "AppKit"
use scripting additions

on run argv
    set filePath to item 1 of argv
    set fileURL to current application's NSURL's fileURLWithPath:filePath
    set pasteboard to current application's NSPasteboard's generalPasteboard()
    pasteboard's clearContents()
    set wroteURL to pasteboard's writeObjects:{fileURL}
    if not (wroteURL as boolean) then error "Could not copy the model file URL"
    set wrotePaths to pasteboard's setPropertyList:{filePath} forType:"NSFilenamesPboardType"
    if not (wrotePaths as boolean) then error "Could not copy the model file list"
end run
"""


def finder_copy_command():
    command = "/usr/bin/osascript"
    if sys.platform == "darwin" and os.access(command, os.X_OK):
        return command
    return None


def _filename_part(value):
    value = "".join(char if char.isalnum() or char in "-_." else "_" for char in value)
    # Two components plus separators fit comfortably within a 255-byte filename.
    return value.strip(".").encode("utf-8")[:96].decode("utf-8", "ignore") or "model"


def write_copy_file(data, directory, script, name):
    directory = Path(directory).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    filename = f"{_filename_part(Path(script).stem)}-{_filename_part(name)}.stl"
    path = directory / filename
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=directory, prefix=".b4dcad-copy-", suffix=".stl", delete=False
        ) as output:
            temporary = Path(output.name)
            output.write(data)
        # Replace atomically, including any preexisting symlink at the output path.
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return path


def copy_file_to_finder(path, command):
    try:
        subprocess.run(
            [command, "-", str(path)],
            input=FINDER_COPY_SCRIPT,
            text=True,
            capture_output=True,
            check=True,
            timeout=15,
        )
    except subprocess.CalledProcessError as error:
        raise RuntimeError(
            (error.stderr or "").strip() or "Could not copy the model file"
        ) from error
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("Finder copy timed out") from error
