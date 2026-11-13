"""Developer check: python -m regwatch.probe [source_id|module ...] [--days N]
Runs the given sources live and prints what they return."""
from __future__ import annotations

import argparse
import sys
import time
import traceback
from datetime import date, timedelta

from .sources import all_sources


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("ids", nargs="*", help="source ids or module/group substrings (default: all)")
    ap.add_argument("--days", type=int, default=30)
    a = ap.parse_args()
    since = date.today() - timedelta(days=a.days)
    srcs = [s for s in all_sources() if not a.ids or any(x in (s.id, type(s).__module__.rsplit('.', 1)[-1]) for x in a.ids)]
    bad = 0
    for s in srcs:
        t = time.time()
        try:
            items = s.fetch(since)
        except Exception:  # noqa: BLE001
            bad += 1
            print(f"\n=== {s.id}  FAILED ({time.time()-t:.1f}s)")
            traceback.print_exc(limit=3)
            continue
        dated = [i for i in items if i.date]
        print(f"\n=== {s.id}  {len(items)} items ({len(dated)} dated, {sum(1 for i in items if i.pdf_url)} with pdf)  {time.time()-t:.1f}s")
        for i in items[:6]:
            print(f"  {i.date}  [{i.doc_type}] {i.ref_no} | {i.title[:90]}\n      url={i.url[:110]}\n      pdf={i.pdf_url[:110]}")
        old = [i for i in dated if i.date < since]
        if old:
            print(f"  !! {len(old)} items older than since={since}")
        if not items:
            print("  !! returned nothing")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
