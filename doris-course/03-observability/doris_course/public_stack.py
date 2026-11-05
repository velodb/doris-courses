"""Make a pinned public checkout readable by non-root container users."""
from pathlib import Path
import stat
import subprocess
import sys


def prepare_public_checkout(root: Path) -> None:
    root = Path(root).resolve()
    entries = subprocess.check_output(["git", "-C", str(root), "ls-tree", "-rz", "HEAD"])
    directories = {root}
    for entry in entries.split(b"\0"):
        if not entry:
            continue
        metadata, name = entry.split(b"\t", 1)
        mode = metadata.split()[0]
        if mode not in {b"100644", b"100755"}:
            continue
        relative = Path(name.decode("utf-8"))
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Invalid public checkout path")
        path = root / relative
        parents = [root / parent for parent in relative.parents]
        if path.is_symlink() or any(parent.is_symlink() for parent in parents):
            continue
        current = path.stat().st_mode
        if stat.S_ISREG(current):
            path.chmod(current | (0o555 if mode == b"100755" else 0o444))
            directories.update(parents)
    for directory in directories:
        directory.chmod(directory.stat().st_mode | 0o555)


if __name__ == "__main__":
    prepare_public_checkout(Path(sys.argv[1]))
