"""Full-text search over downloaded PDFs (and titles), stored in SQLite FTS5 inside history.db.

Tables: pdf_text (one row per indexed item, body = extracted text, plus status so unreadable / scanned
PDFs are not retried every run), pdf_fts (FTS5 over pdf_text.body, external content) and title_fts
(FTS5 over items.title / ref_no, kept in step with the items table, so items without PDFs are found too).
"""
from __future__ import annotations

import logging
import re
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

log = logging.getLogger("notified")

MAX_PAGES = 60          # pages read per PDF
PDF_SECONDS = 20.0      # stop reading a PDF's further pages after this long
RUN_SECONDS = 600.0     # stop indexing for this run after this long (the rest is picked up next run)
MIN_CHARS = 200         # less text than this = scanned / image-only PDF
MAX_CHARS = 2_000_000
HL_START, HL_END = "«", "»"   # « »
_TOKENIZE = "porter unicode61 remove_diacritics 2"

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS pdf_text (
  uid TEXT PRIMARY KEY, body TEXT NOT NULL DEFAULT '', path TEXT, size INTEGER, pages INTEGER,
  status TEXT, indexed_at TEXT
);
CREATE VIRTUAL TABLE IF NOT EXISTS pdf_fts USING fts5(body, content='pdf_text', content_rowid='rowid',
  tokenize='{_TOKENIZE}');
CREATE VIRTUAL TABLE IF NOT EXISTS title_fts USING fts5(uid UNINDEXED, title, ref_no, tokenize='{_TOKENIZE}');
"""


def extract_text(path: Path, max_pages: int = MAX_PAGES, max_seconds: float = PDF_SECONDS) -> tuple[str, int]:
    """(text, pages read). Raises on unreadable files."""
    from pypdf import PdfReader
    logging.getLogger("pypdf").setLevel(logging.ERROR)
    reader = PdfReader(str(path), strict=False)
    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception:  # noqa: BLE001
            pass
    parts, t0, n = [], time.monotonic(), 0
    for n, page in enumerate(reader.pages[:max_pages], 1):
        try:
            parts.append(page.extract_text() or "")
        except Exception as e:  # noqa: BLE001 - one bad page must not lose the rest
            log.debug("page %s of %s: %s", n, path, e)
        if time.monotonic() - t0 > max_seconds:
            break
    text = "\n".join(parts)
    # pypdf can emit lone surrogates (broken emoji / CMap glyphs) that SQLite cannot store as UTF-8
    text = text.encode("utf-16", "surrogatepass").decode("utf-16", "replace")
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)          # re-join hyphenated line breaks
    text = re.sub(r"[ \t ]+", " ", text)
    text = re.sub(r"\s*\n\s*", "\n", text).strip()
    return text[:MAX_CHARS], n


def _fts_query(q: str) -> str:
    """User text -> safe FTS5 query: quoted phrases kept, other words ANDed, trailing * allowed for prefixes."""
    out = []
    for phrase, word in re.findall(r'"([^"]+)"|(\S+)', q):
        if phrase:
            toks = re.findall(r"\w+", phrase)
            if toks:
                out.append('"' + " ".join(toks) + '"')
        else:
            prefix = word.endswith("*")
            for t in re.findall(r"\w+", word):
                out.append(f'"{t}"' + ("*" if prefix else ""))
    return " ".join(out)


def _query_tiers(q: str) -> list[str]:
    """FTS queries to try in order. Plain multi-word input -> [phrase, NEAR(words, 10), all words anywhere]."""
    full = _fts_query(q)
    if not full:
        return []
    if '"' in q or "*" in q:
        return [full]
    words = re.findall(r"\w+", q)
    if len(words) < 2:
        return [full]
    quoted = " ".join(f'"{w}"' for w in words)
    return ['"' + " ".join(words) + '"', f"NEAR({quoted}, 10)", full]


class PdfIndex:
    def __init__(self, db_path: Path):
        self.db = sqlite3.connect(db_path, timeout=30)
        self.db.executescript(SCHEMA)
        self.db.commit()

    def close(self):
        self.db.close()

    # ------------------------------------------------------------ indexing
    def _sync_titles(self):
        if not self.db.execute("SELECT 1 FROM sqlite_master WHERE name='items'").fetchone():
            return
        self.db.execute("INSERT INTO title_fts(uid, title, ref_no) SELECT uid, title, ref_no FROM items "
                        "WHERE uid NOT IN (SELECT uid FROM title_fts)")
        self.db.commit()

    def _store(self, uid: str, body: str, path: str, size: int, pages: int, status: str):
        old = self.db.execute("SELECT rowid, body FROM pdf_text WHERE uid=?", (uid,)).fetchone()
        if old:
            if old[1]:
                self.db.execute("INSERT INTO pdf_fts(pdf_fts, rowid, body) VALUES('delete', ?, ?)", old)
            self.db.execute("DELETE FROM pdf_text WHERE uid=?", (uid,))
        cur = self.db.execute("INSERT INTO pdf_text(uid, body, path, size, pages, status, indexed_at) "
                              "VALUES (?,?,?,?,?,?,?)",
                              (uid, body, path, size, pages, status, datetime.now().isoformat(timespec="seconds")))
        if body:
            self.db.execute("INSERT INTO pdf_fts(rowid, body) VALUES (?, ?)", (cur.lastrowid, body))

    def pending(self, rows: list[dict]) -> list[tuple[dict, Path, int]]:
        done = {u: (p, s) for u, p, s in self.db.execute("SELECT uid, path, size FROM pdf_text")}
        out = []
        for r in rows:
            lp = r.get("local_pdf")
            if not lp:
                continue
            p = Path(lp)
            try:
                size = p.stat().st_size
            except OSError:
                continue
            if done.get(r["uid"]) != (str(p), size):  # new, or the file was replaced
                out.append((r, p, size))
        return out

    def index_pending(self, rows: list[dict], progress: Callable[[str], None] | None = None,
                      cancel: Callable[[], bool] | None = None, max_seconds: float = RUN_SECONDS) -> int:
        """Extract and index text of rows whose local_pdf exists and isn't indexed yet. Returns PDFs indexed
        with text (scanned / unreadable ones are recorded so they are not retried, but not counted)."""
        self._sync_titles()
        todo = self.pending(rows)
        if not todo:
            return 0
        say = progress or (lambda m: None)
        t0, ok = time.monotonic(), 0
        for n, (r, p, size) in enumerate(todo, 1):
            if (cancel and cancel()) or time.monotonic() - t0 > max_seconds:
                say(f"  Indexing stopped after {n - 1}/{len(todo)} (rest next run)")
                break
            try:
                text, pages = extract_text(p)
                status = "ok" if len(text) >= MIN_CHARS else "no_text"
            except Exception as e:  # noqa: BLE001 - corrupt / unsupported PDF
                log.warning("index failed %s: %s", p, e)
                text, pages, status = "", 0, "error"
            try:
                self._store(r["uid"], text if status == "ok" else "", str(p), size, pages, status)
            except (sqlite3.Error, UnicodeError) as e:  # one odd PDF must not stop the rest
                log.warning("index store failed %s: %s", p, e)
                status = "error"
                self._store(r["uid"], "", str(p), size, pages, status)
            ok += status == "ok"
            if n % 10 == 0 or n == len(todo):
                self.db.commit()
                say(f"  Indexing PDFs {n}/{len(todo)}")
        self.db.commit()
        return ok

    # ------------------------------------------------------------ searching
    def search(self, query: str, limit: int = 50) -> list[dict]:
        """[{uid, snippet, rank, title, date, regulator, source_id, ref_no, in_title, in_pdf}], best first
        (lower rank = better). Matches the PDF text and titles / ref numbers; the match is wrapped in « ».
        Several plain words: exact phrase hits come first, then the words close together, then anywhere."""
        tiers = _query_tiers(query)
        if not tiers:
            return []
        self._sync_titles()
        hits: dict[str, dict] = {}
        for tier, q in enumerate(tiers):
            try:
                body = self.db.execute(
                    f"SELECT t.uid, snippet(pdf_fts, 0, '{HL_START}', '{HL_END}', '…', 14), bm25(pdf_fts) "
                    "FROM pdf_fts JOIN pdf_text t ON t.rowid = pdf_fts.rowid WHERE pdf_fts MATCH ? "
                    "ORDER BY bm25(pdf_fts) LIMIT ?", (q, limit * 3)).fetchall()
                title = self.db.execute(
                    f"SELECT uid, highlight(title_fts, 1, '{HL_START}', '{HL_END}'), bm25(title_fts, 0, 1.0, 0.5) "
                    "FROM title_fts WHERE title_fts MATCH ? ORDER BY bm25(title_fts, 0, 1.0, 0.5) LIMIT ?",
                    (q, limit * 3)).fetchall()
            except sqlite3.OperationalError as e:
                log.warning("search %r failed: %s", query, e)
                continue
            found: dict[str, dict] = {}
            for uid, snip, rank in body:
                found[uid] = dict(uid=uid, snippet=" ".join(snip.split()), rank=rank, in_pdf=True, in_title=False)
            for uid, snip, rank in title:
                h = found.setdefault(uid, dict(uid=uid, snippet=snip, rank=0.0, in_pdf=False))
                h["in_title"] = True
                h["rank"] += 3 * rank   # bm25 is negative (lower = better): a title hit weighs most
            for uid, h in found.items():
                if uid not in hits:      # keep the best (earliest) tier for each item
                    h["rank"] += 1000 * tier
                    hits[uid] = h
            if len(hits) >= limit:
                break
        if not hits:
            return []
        marks = ",".join("?" * len(hits))
        meta = {r[0]: r[1:] for r in self.db.execute(
            f"SELECT uid, title, date, regulator, source_id, ref_no FROM items WHERE uid IN ({marks})", list(hits))}
        out = []
        for h in hits.values():
            m = meta.get(h["uid"])
            if not m:
                continue  # stale index entry for an item no longer in history
            h.update(title=m[0], date=m[1], regulator=m[2], source_id=m[3], ref_no=m[4])
            out.append(h)
        out.sort(key=lambda h: h["rank"])
        return out[:limit]
