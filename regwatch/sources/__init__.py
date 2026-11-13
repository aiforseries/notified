"""Source registry. Each module listed in MODULES defines Source subclasses in its SOURCES tuple.
(Explicit list rather than auto-discovery so PyInstaller bundles every module.)"""
from __future__ import annotations

import importlib
import logging

from .base import Source

MODULES = ["icai_audit", "mca", "direct_tax", "gst_customs", "sebi", "rbi", "labour", "data_protection", "pib", "egazette"]

log = logging.getLogger(__name__)


def all_sources() -> list[Source]:
    out: list[Source] = []
    for name in MODULES:
        try:
            mod = importlib.import_module(f"{__name__}.{name}")
        except ModuleNotFoundError as e:
            if e.name == f"{__name__}.{name}":
                continue
            raise
        for cls in getattr(mod, "SOURCES", ()):
            out.append(cls())
    return out
