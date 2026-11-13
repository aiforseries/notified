"""Orchestrates a run: fetch sources in parallel -> tag -> history -> optional PDFs -> Excel."""
from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable

from . import pdfs
from .excel import write_tracker
from .paths import config_dir, default_output_dir
from .sources import all_sources
from .store import Store
from .tagger import Tagger, ensure_rules

log = logging.getLogger("regwatch")
TRACKER_NAME = "Notified_Tracker.xlsx"
LEGACY_TRACKER = "RegWatch_Tracker.xlsx"


def migrate_tracker(output_dir: Path) -> None:
    """Carry an existing RegWatch tracker (with the team's Status/Owner/Notes) over to the new name."""
    old, new = output_dir / LEGACY_TRACKER, output_dir / TRACKER_NAME
    if old.exists() and not new.exists():
        try:
            import shutil
            shutil.copy2(old, new)
        except OSError:
            pass
# Regulators often post documents days or weeks after their issue date. Each source looks back this many
# extra days; an older-dated item is kept only if that source has run before and has never seen it.
LATE_BUFFER_DAYS = 45
LATE_TAG = "Posted late"


def clean_text(s: str) -> str:
    """Tidy a title: strip a stray ".pdf" file suffix; U+FFFD (a lost curly quote or dash) -> dash or quote."""
    s = re.sub(r"\.pdf\s*$", "", s, flags=re.I).strip()
    if "�" not in s:
        return s
    s = re.sub(r"\s�\s", " - ", s)
    return s.replace("�", "'")


def _apply_baseline(store: Store, source_id: str, items: list) -> list:
    """For sources whose posts carry no date ("What's New" boxes). First run: keep undated items that mention
    the current year / financial year, remember the rest. Later runs: undated items not seen before are new."""
    key = f"baseline:{source_id}"
    raw = store.kv_get(key, "")
    first = raw == ""
    baseline = set(json.loads(raw)) if raw else set()
    y = date.today().year
    current = re.compile(rf"\b({y}|{y - 1}-{str(y)[2:]}|{y}-{str(y + 1)[2:]}|{y - 1}-{y}|{y}-{y + 1})\b")
    keep = []
    for i in items:
        if i.date:
            keep.append(i)
            continue
        if i.uid in baseline:
            continue
        baseline.add(i.uid)
        if not first or current.search(i.title):
            keep.append(i)
    store.kv_set(key, json.dumps(sorted(baseline)))
    return keep


@dataclass
class Settings:
    output_dir: str = str(default_output_dir())
    days: int = 30
    enabled: list[str] = field(default_factory=list)       # source ids; empty = defaults
    pdf_regulators: list[str] = field(default_factory=list)
    known_ids: list[str] = field(default_factory=list)    # sources that existed when `enabled` was saved
    theme: str = "system"
    auto_check: str = "off"           # off | daily | weekly
    auto_time: str = "09:00"
    auto_weekday: int = 0             # 0 = Monday
    tray_on_close: bool = False       # keep running in the tray when the window is closed
    notify: bool = True               # Windows notification after a scheduled check
    check_updates: bool = True        # look for a newer Notified on GitHub at start-up
    last_auto_run: str = ""           # ISO datetime of the last scheduled check                                  # light | dark | system

    @classmethod
    def load(cls) -> "Settings":
        p = config_dir() / "settings.json"
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        except Exception:  # noqa: BLE001
            return cls()

    def save(self):
        (config_dir() / "settings.json").write_text(json.dumps(self.__dict__, indent=2), encoding="utf-8")


@dataclass
class RunResult:
    tracker: Path
    new_count: int
    total_in_period: int
    pdfs_saved: int
    failures: list[str]


def run(since: date, source_ids: list[str], pdf_regulators: list[str], output_dir: Path,
        progress: Callable[[str], None] = print, cancel: Callable[[], bool] = lambda: False) -> RunResult:
    output_dir.mkdir(parents=True, exist_ok=True)
    migrate_tracker(output_dir)
    tagger = Tagger(ensure_rules(output_dir))
    sources = {s.id: s for s in all_sources()}
    chosen = [sources[i] for i in source_ids if i in sources]
    run_ts = datetime.now().isoformat(timespec="seconds")
    progress(f"Fetching {len(chosen)} sources since {since:%d-%b-%Y} ...")
    store = Store(output_dir / "history.db")
    known = {s.id: store.known(s.id) for s in chosen}
    covered = {s.id: store.covered_from(s.id) for s in chosen}  # earliest date already scanned, or None
    fetch_since = since - timedelta(days=LATE_BUFFER_DAYS)

    def in_scope(i, sid) -> bool:
        if not i.date or i.date >= since:
            return True
        # Older-dated: keep only if an earlier run already scanned that date and didn't see it (= posted late).
        c = covered[sid]
        return c is not None and i.date >= max(c, fetch_since) and i.uid not in known[sid]

    results, run_log = {}, []
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(s.fetch, fetch_since): s for s in chosen}
        for f in as_completed(futs):
            s = futs[f]
            try:
                items = [i for i in f.result() if in_scope(i, s.id)]
                for i in items:
                    i.title = clean_text(i.title)
                if s.undated_baseline:
                    items = _apply_baseline(store, s.id, items)
                results[s.id] = items
                progress(f"  OK    {s.name}: {len(items)}")
            except Exception as e:  # noqa: BLE001 - one site failing must not stop the run
                log.exception("source %s failed", s.id)
                results[s.id] = None
                progress(f"  FAIL  {s.name}: {type(e).__name__}: {str(e)[:120]}")
                run_log.append(dict(group=s.group, name=s.name, status="ERROR", count=0, new=0,
                                    message=f"{type(e).__name__}: {str(e)[:200]}", homepage=s.homepage))
            if cancel():
                ex.shutdown(cancel_futures=True)
                break

    all_items = [i for items in results.values() if items for i in items]
    for it in all_items:
        it.tags = tagger.tag(it)
        if it.date and it.date < since:
            it.tags.append(LATE_TAG)
    new_uids = store.upsert(all_items, run_ts)
    for sid, items in results.items():
        if items is not None:
            store.mark_covered(sid, since)  # only the requested window counts as scanned
    for s in chosen:
        items = results.get(s.id)
        if items is not None:
            run_log.append(dict(group=s.group, name=s.name, status="OK", count=len(items),
                                new=sum(1 for i in items if i.uid in new_uids), message="", homepage=s.homepage))
    run_log.sort(key=lambda e: (e["group"], e["name"]))

    saved = 0
    if pdf_regulators and not cancel():
        want = [i for i in all_items if i.regulator in pdf_regulators]
        progress(f"Downloading PDFs for {', '.join(pdf_regulators)} ({len(want)} items) ...")
        rows = {r["uid"]: r for r in store.all_rows()}
        for n, it in enumerate(want, 1):
            if cancel():
                break
            row = rows.get(it.uid)
            if not row:
                continue
            dest = pdfs.target_path(output_dir, row)
            if row.get("local_pdf") and Path(row["local_pdf"]).exists():
                continue
            try:
                url = it.pdf_url or row["pdf_url"] or sources[it.source_id].resolve_pdf(it)
                if url and pdfs.download(url, dest):
                    store.set_pdf(it.uid, url, str(dest))
                    saved += 1
            except Exception as e:  # noqa: BLE001
                log.warning("pdf failed %s: %s", it.url, e)
            if n % 10 == 0:
                progress(f"  PDFs: {n}/{len(want)}")
        progress(f"  {saved} PDFs saved")
        try:  # full-text index of the saved PDFs (search never blocks writing the tracker)
            from .search import PdfIndex
            idx = PdfIndex(output_dir / "history.db")
            todo = len(idx.pending(store.all_rows()))
            if todo and not cancel():
                progress(f"Indexing PDFs 0/{todo} ...")
                n_ok = idx.index_pending(store.all_rows(), progress=progress, cancel=cancel)
                progress(f"  {n_ok} PDFs indexed for search")
            idx.close()
        except Exception as e:  # noqa: BLE001
            log.warning("pdf indexing failed: %s", e)
            progress(f"  PDF indexing skipped: {type(e).__name__}: {str(e)[:120]}")

    progress("Writing Excel tracker ...")
    run_uids = {i.uid for i in all_items}
    store.set_last_run(run_uids, since, run_ts)
    store.kv_set("run_log", json.dumps(run_log))
    from .usercols import merged
    tracker = write_tracker(output_dir / TRACKER_NAME, store.all_rows(), new_uids, run_log, sources,
                            run_uids=run_uids, user_cols=merged(store, output_dir / TRACKER_NAME))
    store.close()
    failures = [e["name"] for e in run_log if e["status"] != "OK"]
    progress(f"Done: {len(new_uids)} new, {len(all_items)} in period, {len(failures)} source(s) failed.")
    return RunResult(tracker, len(new_uids), len(all_items), saved, failures)


def rewrite_tracker(output_dir: Path) -> Path | None:
    """Re-write the tracker without fetching, when Status/Owner/Notes were edited in the app after the
    tracker was last saved. Returns the path written, or None if nothing needed doing."""
    migrate_tracker(output_dir)
    tracker = output_dir / TRACKER_NAME
    db = output_dir / "history.db"
    if not db.exists():
        return None
    store = Store(db)
    try:
        meta = store.user_meta()
        mtime = datetime.fromtimestamp(tracker.stat().st_mtime) if tracker.exists() else None
        if mtime and not any(m.get("updated") and datetime.fromisoformat(m["updated"]) > mtime for m in meta.values()):
            return None
        from .usercols import merged
        run_uids, _, run_ts = store.last_run()
        rows = store.all_rows()
        new_uids = {r["uid"] for r in rows if r["first_seen"] and r["first_seen"].isoformat(timespec="seconds") == run_ts}
        run_log = json.loads(store.kv_get("run_log", "[]"))
        return write_tracker(tracker, rows, new_uids, run_log, {s.id: s for s in all_sources()},
                             run_uids=run_uids or None, user_cols=merged(store, tracker))
    finally:
        store.close()
