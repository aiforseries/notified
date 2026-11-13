"""Checks GitHub Releases for a newer Notified and downloads it on request.

Privacy: one anonymous GET to the public GitHub API; nothing about the user is sent.
Can be turned off in the app (Settings.check_updates)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import requests

from . import __version__

REPO = "aiforseries/notified"
API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"


@dataclass
class Release:
    version: str
    notes: str
    page: str
    exe_url: str
    exe_size: int


def _ver(s: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", s)[:3]) or (0,)


def latest(timeout: int = 8) -> Release | None:
    """Newer release than the running one, or None (also None on any network error)."""
    try:
        r = requests.get(API, timeout=timeout, headers={"Accept": "application/vnd.github+json",
                                                         "User-Agent": f"Notified/{__version__}"})
        if r.status_code != 200:
            return None
        d = r.json()
    except Exception:  # noqa: BLE001 - offline / rate-limited: just skip
        return None
    tag = d.get("tag_name") or ""
    if _ver(tag) <= _ver(__version__):
        return None
    exe = next((a for a in d.get("assets", []) if a.get("name", "").lower().endswith(".exe")), None)
    return Release(version=tag.lstrip("v"), notes=(d.get("body") or "").strip(), page=d.get("html_url") or RELEASES_PAGE,
                   exe_url=exe["browser_download_url"] if exe else "", exe_size=exe.get("size", 0) if exe else 0)


def download(rel: Release, folder: Path, progress=None) -> Path:
    """Download the new exe as Notified-<version>.exe into `folder` (the user's Downloads by default)."""
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / f"Notified-{rel.version}.exe"
    tmp = dest.with_suffix(".part")
    with requests.get(rel.exe_url, stream=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length") or rel.exe_size or 0)
        got = 0
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(1 << 16):
                f.write(chunk)
                got += len(chunk)
                if progress and total:
                    progress(got / total)
    tmp.replace(dest)
    return dest
