"""History of every item ever fetched (SQLite), so the tracker accumulates and 'new' can be flagged."""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from pathlib import Path

from .models import Item

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
  uid TEXT PRIMARY KEY, source_id TEXT, regulator TEXT, title TEXT, date TEXT, url TEXT,
  doc_type TEXT, ref_no TEXT, pdf_url TEXT, tags TEXT, first_seen TEXT, local_pdf TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS coverage (source_id TEXT PRIMARY KEY, covered_from TEXT);
"""


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.executescript(SCHEMA)

    def upsert(self, items: list[Item], run_ts: str) -> set[str]:
        """Insert/refresh items; returns uids that were never seen before."""
        new = set()
        for it in items:
            row = self.db.execute("SELECT uid FROM items WHERE uid=?", (it.uid,)).fetchone()
            vals = (it.source_id, it.regulator, it.title, it.date.isoformat() if it.date else "", it.url,
                    it.doc_type, it.ref_no, it.pdf_url, json.dumps(it.tags))
            if row:
                self.db.execute("UPDATE items SET source_id=?,regulator=?,title=?,date=?,url=?,doc_type=?,ref_no=?,"
                                "pdf_url=COALESCE(NULLIF(?,''),pdf_url),tags=? WHERE uid=?", (*vals, it.uid))
            else:
                self.db.execute("INSERT INTO items VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (it.uid, *vals, run_ts, ""))
                new.add(it.uid)
        self.db.commit()
        return new

    def known(self, source_id: str) -> set[str]:
        """UIDs already recorded for a source (empty set = source never run before)."""
        return {r[0] for r in self.db.execute("SELECT uid FROM items WHERE source_id=?", (source_id,))}

    def covered_from(self, source_id: str) -> date | None:
        """Earliest date this source has been scanned from (falls back to its oldest stored item)."""
        r = self.db.execute("SELECT covered_from FROM coverage WHERE source_id=?", (source_id,)).fetchone()
        if not r:
            r = self.db.execute("SELECT MIN(date) FROM items WHERE source_id=? AND date<>''", (source_id,)).fetchone()
        return date.fromisoformat(r[0]) if r and r[0] else None

    def mark_covered(self, source_id: str, since: date):
        cur = self.covered_from(source_id)
        val = min(cur, since) if cur else since
        self.db.execute("INSERT OR REPLACE INTO coverage VALUES (?,?)", (source_id, val.isoformat()))
        self.db.commit()

    def set_pdf(self, uid: str, pdf_url: str, local: str):
        self.db.execute("UPDATE items SET pdf_url=COALESCE(NULLIF(?,''),pdf_url), local_pdf=? WHERE uid=?", (pdf_url, local, uid))
        self.db.commit()

    def all_rows(self) -> list[dict]:
        cur = self.db.execute("SELECT * FROM items ORDER BY date DESC, first_seen DESC")
        cols = [c[0] for c in cur.description]
        out = []
        for r in cur.fetchall():
            d = dict(zip(cols, r))
            d["tags"] = json.loads(d["tags"] or "[]")
            d["date"] = date.fromisoformat(d["date"]) if d["date"] else None
            d["first_seen"] = datetime.fromisoformat(d["first_seen"]) if d["first_seen"] else None
            out.append(d)
        return out

    def close(self):
        self.db.close()
