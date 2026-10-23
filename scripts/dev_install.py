"""Install dependencies and CLI entry points, then link the package to source."""

from pathlib import Path
import shutil
import subprocess
import sys
import sysconfig


def main():
    project = Path(__file__).resolve().parent.parent
    package = Path(sysconfig.get_path("purelib")) / "b4dcad"

    # Do not let pip uninstall files through a previous development symlink.
    if package.is_symlink():
        package.unlink()

    subprocess.run([sys.executable, "-m", "pip", "install", str(project)], check=True)
    shutil.rmtree(package)
    package.symlink_to(project / "b4dcad", target_is_directory=True)
    print(f"Linked {package} -> {project / 'b4dcad'}")


if __name__ == "__main__":
    main()
