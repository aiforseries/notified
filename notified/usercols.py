"""Status / Owner / Notes can be edited in two places: the Excel tracker and the in-app browser.
Rule: whichever was changed more recently wins (app edit time vs the tracker file's modified time)."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .excel import USER_COLS, _read_user_columns
from .store import Store


def merged(store: Store, tracker: Path) -> dict[str, dict[str, str]]:
    excel_vals = _read_user_columns(tracker) if tracker.exists() else {}
    mtime = datetime.fromtimestamp(tracker.stat().st_mtime) if tracker.exists() else None
    out = {u: dict(v) for u, v in excel_vals.items()}
    for uid, m in store.user_meta().items():
        edited = datetime.fromisoformat(m["updated"]) if m.get("updated") else None
        if uid not in out or mtime is None or (edited and edited > mtime):
            out[uid] = {c: m.get(c, "") for c in USER_COLS}
    return out
