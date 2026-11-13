"""Source health check: fetch every source for the last N days and report which ones fail.

Usage: python tools/health.py [--days 30] [--report health.md]
Exit code 1 if any source fails. Used by .github/workflows/health.yml (daily)."""
from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from notified.sources import all_sources  # noqa: E402

# These sites refuse connections from outside India (seen from GitHub's runners: resets, 403s, timeouts),
# though they work normally from Indian networks. The cloud check reports them as "not checked" instead of
# failing; run this script locally (or rely on the tracker's Sources sheet) to check them.
INDIA_ONLY_PREFIXES = ("cbic_", "dgft_", "epfo_", "esic_")


def check(src, since):
    t = time.time()
    try:
        items = src.fetch(since)
        return src, "ok", len(items), "", time.time() - t
    except Exception as e:  # noqa: BLE001 - we want every failure in the report
        return src, "fail", 0, f"{type(e).__name__}: {str(e)[:180]}", time.time() - t


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--report", default="health.md")
    ap.add_argument("--outside-india", action="store_true",
                    help="running outside India (e.g. GitHub Actions): skip sites that block foreign traffic")
    a = ap.parse_args()
    since = date.today() - timedelta(days=a.days)
    sources = all_sources()
    skipped = [s for s in sources if a.outside_india and s.id.startswith(INDIA_ONLY_PREFIXES)]
    sources = [s for s in sources if s not in skipped]
    results = []
    with ThreadPoolExecutor(max_workers=6) as ex:
        for f in as_completed([ex.submit(check, s, since) for s in sources]):
            results.append(f.result())
    results.sort(key=lambda r: (r[1] == "ok", r[0].group, r[0].name))
    failed = [r for r in results if r[1] == "fail"]
    empty = [r for r in results if r[1] == "ok" and r[2] == 0]

    lines = [f"Checked {len(results)} sources on {date.today():%d %b %Y} (items since {since:%d %b %Y}).", ""]
    lines.append(f"**{len(failed)} failing**, {len(results) - len(failed)} working"
                 + (f" ({len(empty)} returned nothing in the period, which can be normal)." if empty else "."))
    if skipped:
        lines += ["", f"Not checked from here ({len(skipped)}): these sites only answer Indian networks. "
                      "Run `python tools/health.py` from India to check them: "
                      + ", ".join(f"`{s.id}`" for s in skipped)]
    if failed:
        lines += ["", "| Source | Error | Listing |", "|---|---|---|"]
        for s, _, _, err, _ in failed:
            lines.append(f"| {s.name} (`{s.id}`) | {err.replace('|', '/')} | {s.homepage} |")
    lines += ["", "<details><summary>All sources</summary>", "", "| Source | Status | Items | Seconds |",
              "|---|---|---|---|"]
    for s, st, n, _, secs in results:
        lines.append(f"| {s.name} | {'OK' if st == 'ok' else 'FAIL'} | {n} | {secs:.1f} |")
    lines += ["", "</details>"]
    Path(a.report).write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines[:3]))
    for s, _, _, err, _ in failed:
        print(f"FAIL {s.id}: {err}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
