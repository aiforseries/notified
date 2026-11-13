from __future__ import annotations

import os
import sys
from pathlib import Path


def resource(name: str) -> Path:
    """Path to a file bundled with the package (works inside the PyInstaller exe)."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent)) / "regwatch"
    return base / name


def default_output_dir() -> Path:
    docs = Path(os.environ.get("USERPROFILE", Path.home())) / "Documents"
    return (docs if docs.exists() else Path.home()) / "RegWatch"


def config_dir() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".config")
    p = base / "RegWatch"
    p.mkdir(parents=True, exist_ok=True)
    return p
