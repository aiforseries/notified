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
    a = ap.parse_args(argv)

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
    ids = a.sources.split(",") if a.sources else (st.enabled or [s.id for s in srcs if s.enabled_by_default])
    pdf = [] if (a.pdf or "").lower() == "none" else (a.pdf.split(",") if a.pdf else st.pdf_regulators)
    res = run(since, ids, pdf, Path(a.out or st.output_dir))
    print(f"Tracker: {res.tracker}")
    return 0 if not res.failures else 2


def main() -> int:
    _setup_logging()
    if len(sys.argv) > 1:
        return cli(sys.argv[1:])
    from .gui import main as gui_main
    gui_main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
