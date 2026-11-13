from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

APP = "Notified"
LEGACY = "RegWatch"  # the app's name before v0.4


def resource(name: str) -> Path:
    """Path to a file bundled with the package (works inside the PyInstaller exe)."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent)) / "regwatch"
    return base / name


def default_output_dir() -> Path:
    docs = Path(os.environ.get("USERPROFILE", Path.home())) / "Documents"
    root = docs if docs.exists() else Path.home()
    if (root / LEGACY).exists() and not (root / APP).exists():
        return root / LEGACY  # keep using an existing folder rather than starting an empty one
    return root / APP


def config_dir() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".config")
    p, old = base / APP, base / LEGACY
    if not p.exists() and old.exists():  # carry settings over from RegWatch
        try:
            shutil.copytree(old, p)
        except OSError:
            pass
    p.mkdir(parents=True, exist_ok=True)
    return p
