"""Python source selection within the preview's initial repository boundary."""

from pathlib import Path


class PreviewFiles:
    def __init__(self, script):
        directory = Path(script).resolve().parent
        self.root = directory
        self.recursive = False
        for parent in (directory, *directory.parents):
            # Worktrees and submodules can use a .git file instead of a directory.
            if (parent / ".git").exists():
                self.root = parent
                self.recursive = True
                break

    def resolve(self, relative):
        if not isinstance(relative, str) or Path(relative).is_absolute():
            raise ValueError("Select a Python file relative to the file tree root")
        path = (self.root / relative).resolve()
        if (
            not path.is_relative_to(self.root)
            or ".git" in path.relative_to(self.root).parts
        ):
            raise ValueError("File is outside the preview repository boundary")
        if path.suffix != ".py" or not path.is_file():
            raise ValueError("Select an existing .py file")
        if not self.recursive and path.parent != self.root:
            raise ValueError("Without Git, select a file in the current directory")
        if self.recursive:
            parent = path.parent
            while parent != self.root:
                if (parent / ".git").exists():
                    raise ValueError("File belongs to a different Git repository")
                parent = parent.parent
        return path

    def tree(self, selected):
        def children(directory):
            folders, files = [], []
            try:
                entries = sorted(directory.iterdir(), key=lambda path: path.name)
            except OSError:
                return []
            for path in entries:
                if path.name == ".git" or path.is_symlink():
                    continue
                relative = path.relative_to(self.root).as_posix()
                if path.is_dir():
                    if not self.recursive or (path / ".git").exists():
                        continue
                    nested = children(path)
                    if nested:
                        folders.append(
                            {"name": path.name, "path": relative, "children": nested}
                        )
                elif path.suffix == ".py" and path.is_file():
                    files.append({"name": path.name, "path": relative})
            return folders + files

        return {
            "root": self.root.name,
            "selected": Path(selected).resolve().relative_to(self.root).as_posix(),
            "files": children(self.root),
        }
