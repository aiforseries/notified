"""Download source PDFs into <out>/PDFs/<Regulator>/<YYYY-MM>/."""
from __future__ import annotations

import re
from pathlib import Path

from . import http

_BAD = re.compile(r'[<>:"/\|?*\x00-\x1f]+')


def safe_name(s: str, limit: int = 110) -> str:
    s = _BAD.sub(" ", s)
    s = " ".join(s.split()).strip(" .")
    return s[:limit].rstrip(" .") or "document"


def target_path(root: Path, row: dict) -> Path:
    d = row["date"]
    folder = root / "PDFs" / safe_name(row["regulator"], 30) / (d.strftime("%Y-%m") if d else "undated")
    stem = f"{d.isoformat() if d else 'undated'} {safe_name(row['ref_no'] + ' ' + row['title'] if row['ref_no'] else row['title'])}"
    return folder / f"{stem}.pdf"


def download(url: str, dest: Path) -> bool:
    """Returns True if a real PDF was saved (skips if already present)."""
    if dest.exists() and dest.stat().st_size > 0:
        return True
    r = http.get(url, timeout=90)
    if r.status_code != 200 or not r.content[:1024].lstrip().startswith(b"%PDF"):
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part")
    tmp.write_bytes(r.content)
    tmp.replace(dest)
    return True
