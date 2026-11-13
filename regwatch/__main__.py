"""Entry point. No arguments -> desktop window. With --cli -> headless run (for Task Scheduler)."""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

from .paths import config_dir


def _setup_logging():
    logging.basicConfig(filename=config_dir() / "regwatch.log", level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def cli(argv: list[str]) -> int:
    from .runner import Settings, run
    from .sources import all_sources

    ap = argparse.ArgumentParser(prog="RegWatch", description="Fetch Indian regulatory updates into an Excel tracker.")
    ap.add_argument("--cli", action="store_true", help="run without the window")
    ap.add_argument("--days", type=int, help="look back N days (default: last setting)")
    ap.add_argument("--since", help="start date dd-mm-yyyy (overrides --days)")
    ap.add_argument("--sources", help="comma-separated source ids (default: last selection in the app)")
    ap.add_argument("--pdf", help="comma-separated regulators to save PDFs for, e.g. SEBI,RBI ('none' to skip)")
    ap.add_argument("--out", help="output folder")
    ap.add_argument("--list", action="store_true", help="list source ids and exit")
    ap.add_argument("--digest", metavar="PATH", help="also write a client digest PDF for the same period")
    ap.add_argument("--digest-only", action="store_true", help="with --digest: skip fetching, use existing history")
    ap.add_argument("--search", metavar="QUERY", help="search titles and saved PDFs in the output folder (no fetching)")
    ap.add_argument("--limit", type=int, default=50, help="max results for --search")
    a = ap.parse_args(argv)
    if a.search is not None:
        return _search(a.search, Path(a.out or Settings.load().output_dir), a.limit)

    srcs = all_sources()
    if a.list:
        for s in srcs:
            print(f"{s.id:32} {s.regulator:8} {s.name}{'' if s.enabled_by_default else '  [off by default]'}")
        return 0
    st = Settings.load()
    if a.since:
        from .dates import parse_date
        since = parse_date(a.since)
        if not since:
            ap.error("--since must be dd-mm-yyyy")
    else:
        days = a.days or (st.days if st.days > 0 else 30)
        since = date.today() - timedelta(days=days)
    out = Path(a.out or st.output_dir)
    if a.digest_only and not a.digest:
        ap.error("--digest-only needs --digest PATH")
    if a.digest_only:
        if not (out / "history.db").exists():
            ap.error(f"no history found in {out} - run once without --digest-only first")
        print(f"Digest: {_digest(out, since, a.digest)}")
        return 0
    ids = a.sources.split(",") if a.sources else (st.enabled or [s.id for s in srcs if s.enabled_by_default])
    pdf = [] if (a.pdf or "").lower() == "none" else (a.pdf.split(",") if a.pdf else st.pdf_regulators)
    res = run(since, ids, pdf, out)
    print(f"Tracker: {res.tracker}")
    if a.digest:
        print(f"Digest: {_digest(out, since, a.digest)}")
    return 0 if not res.failures else 2


def _digest(out: Path, since: date, path: str) -> Path:
    from .digest import build_digest
    from .store import Store
    from .runner import TRACKER_NAME
    from .tagger import ensure_rules
    from .usercols import merged
    store = Store(out / "history.db")
    try:
        return build_digest(store.all_rows(), since, date.today(), Path(path),
                            user_cols=merged(store, out / TRACKER_NAME), rules_file=ensure_rules(out))
    finally:
        store.close()


def _search(query: str, out_dir: Path, limit: int) -> int:
    from .search import PdfIndex
    db = out_dir / "history.db"
    if not db.exists():
        print(f"No history.db in {out_dir} - run a fetch first (or pass --out).")
        return 1
    idx = PdfIndex(db)
    try:
        hits = idx.search(query, limit=limit)
    finally:
        idx.close()
    print(f"{len(hits)} result(s) for {query!r}")
    for h in hits:
        where = "+".join(w for w, f in (("title", h["in_title"]), ("pdf", h["in_pdf"])) if f)
        print(f"\n{h['date'] or 'undated':10}  {h['regulator']:6}  {h['title'][:110]}  [{where}]  uid={h['uid']}")
        print(f"    {h['snippet'][:300]}")
    return 0


def main() -> int:
    _setup_logging()
    args = sys.argv[1:]
    if args and args != ["--tray"]:
        return cli(args)
    from .webapp import main as gui_main
    gui_main(start_hidden=args == ["--tray"])  # --tray: launched at Windows start-up, stay in the tray
    return 0


if __name__ == "__main__":
    sys.exit(main())
