"""Orchestrates a run: fetch sources in parallel -> tag -> history -> optional PDFs -> Excel."""
from __future__ import annotations

import json
import logging
import re
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable

from . import pdfs
from .excel import write_tracker
from .paths import config_dir, default_output_dir, resource
from .sources import all_sources
from .store import Store
from .tagger import Tagger

log = logging.getLogger("regwatch")
TRACKER_NAME = "RegWatch_Tracker.xlsx"
# Regulators often post documents days or weeks after their issue date. Each source looks back this many
# extra days; an older-dated item is kept only if that source has run before and has never seen it.
LATE_BUFFER_DAYS = 45
LATE_TAG = "Posted late"


def clean_text(s: str) -> str:
    """Some sites emit U+FFFD where a curly quote or dash was. Spaced -> dash, otherwise -> quote."""
    if "�" not in s:
        return s
    s = re.sub(r"\s�\s", " - ", s)
    return s.replace("�", "'")


@dataclass
class Settings:
    output_dir: str = str(default_output_dir())
    days: int = 30
    enabled: list[str] = field(default_factory=list)       # source ids; empty = defaults
    pdf_regulators: list[str] = field(default_factory=list)
    known_ids: list[str] = field(default_factory=list)    # sources that existed when `enabled` was saved
    theme: str = "system"                                  # light | dark | system

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
    rules = output_dir / "tag_rules.json"
    if not rules.exists():
        shutil.copy(resource("tag_rules.json"), rules)
    tagger = Tagger(rules)
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

    progress("Writing Excel tracker ...")
    tracker = write_tracker(output_dir / TRACKER_NAME, store.all_rows(), new_uids, run_log, sources,
                            run_uids={i.uid for i in all_items})
    store.close()
    failures = [e["name"] for e in run_log if e["status"] != "OK"]
    progress(f"Done: {len(new_uids)} new, {len(all_items)} in period, {len(failures)} source(s) failed.")
    return RunResult(tracker, len(new_uids), len(all_items), saved, failures)
