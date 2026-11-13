"""Direct Tax (CBDT): notifications, circulars and press releases as published on the
Income-tax e-filing portal's "Latest News" listing (https://www.incometax.gov.in/iec/foportal/latest-news).

Why this source: incometaxindia.gov.in (the CBDT's own notification/circular/press-release lists)
answers ordinary requests with HTTP 403 bot protection. We deliberately do NOT work around that.
The e-filing portal page is openly served to plain requests, is a server-rendered Drupal view
(10 rows/page, newest first, paged with ?page=0,N) and links each CBDT notification/circular PDF.
It is a curated feed, so it may not carry every notification CBDT issues (e.g. routine
section 10(46)/80G notifications); users should still check incometaxindia.gov.in by hand.

Covers both the Income-tax Act, 1961 and the Income-tax Act, 2025 (in force from 1 Apr 2026);
the Act is not split out because the portal mixes both in one list.
"""
from __future__ import annotations

import re
import time
from datetime import date

import requests
from bs4 import BeautifulSoup

from .. import http
from ..dates import find_date, parse_date
from .base import Source

PORTAL = "https://www.incometax.gov.in"
LATEST = PORTAL + "/iec/foportal/latest-news"
MAX_PAGES = 15
_CACHE: dict = {}
_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Notified",
       "Accept": "text/html,application/xhtml+xml"}

_NUM = r"(\d{1,4})\s*(?:/|of)\s*(\d{4})"
_NOTI_RX = re.compile(r"Notification\s*No\.?\s*" + _NUM, re.I)
_CIRC_RX = re.compile(r"Circular\s*No\.?\s*" + _NUM, re.I)
_FILE_RX = re.compile(r"(notification|circular)[-_ ]*(?:no[-_ .]*)?(\d{1,4})[-_ ]+(?:of[-_ ]+)?(\d{4})", re.I)
_PR_RX = re.compile(r"press\s*release", re.I)


def _clean(s) -> str:
    return " ".join(str(s or "").split()).strip()


def _get(url: str):
    # Plain requests (no browser impersonation): this portal serves ordinary clients.
    r = requests.get(url, headers=_UA, timeout=http.DEFAULT_TIMEOUT)
    r.raise_for_status()
    return BeautifulSoup(r.text, "lxml")


def _classify(text: str, pdf: str) -> tuple[str, str]:
    """Return (doc_type, ref_no) from the PDF file name, else the row text."""
    f = _FILE_RX.search(pdf.rsplit("/", 1)[-1])
    if f:
        kind = f.group(1).capitalize()
        return kind, f"{kind} No. {int(f.group(2)):02d}/{f.group(3)}"
    m, c = _NOTI_RX.search(text), _CIRC_RX.search(text)
    if c and (not m or c.start() < m.start()):
        return "Circular", f"Circular No. {int(c.group(1)):02d}/{c.group(2)}"
    if m:
        return "Notification", f"Notification No. {int(m.group(1)):02d}/{m.group(2)}"
    if _PR_RX.search(text) or "press" in pdf.lower():
        return "Press Release", ""
    return "Update", ""


class _LatestNews(Source):
    group = "Direct Tax"
    regulator = "CBDT"
    homepage = LATEST
    keep: tuple[str, ...] = ()   # doc_types this feed keeps

    def _rows(self, since: date):
        for page in range(MAX_PAGES):
            s = _get(LATEST if page == 0 else f"{LATEST}?page=0,{page}")
            rows = s.select(".view-latest-news-view .views-row") or s.select(".views-row")
            if not rows:
                return
            past = False
            for row in rows:
                d_el = row.select_one(".up-date")
                d = parse_date(_clean(d_el.get_text())) if d_el else None
                body = row.select_one(".gry-ft") or row
                text = _clean(body.get_text(" "))
                text = re.sub(r"\s*Click here\.?\s*$", "", text, flags=re.I).strip()
                if d is None:
                    d = find_date(text)
                if d and d < since:
                    past = True
                    continue
                links = [http.absolute(LATEST, a.get("href")) for a in body.find_all("a", href=True)]
                pdf = next((h for h in links if h.lower().split("?")[0].endswith(".pdf")), "")
                yield d, text, pdf, (links[0] if links else "")
            if past or len(rows) < 10:
                return

    def fetch(self, since: date) -> list:
        # The three CBDT feeds share one listing; fetch it once per run (5-minute cache).
        hit = _CACHE.get(since)
        if hit and time.time() - hit[0] < 300:
            rows = hit[1]
        else:
            rows = list(self._rows(since))
            _CACHE[since] = (time.time(), rows)
        out, seen = [], set()
        for d, text, pdf, link in rows:
            if not text:
                continue
            doc_type, ref = _classify(text, pdf)
            if self.keep and doc_type not in self.keep:
                continue
            title = text if len(text) <= 300 else text[:297].rstrip() + "..."
            it = self.item(title=title, date=d, url=pdf or link or LATEST, doc_type=doc_type,
                           ref_no=ref, pdf_url=pdf)
            if it.uid in seen:
                continue
            seen.add(it.uid)
            out.append(it)
        return out


class CbdtNotifications(_LatestNews):
    id = "cbdt_notifications"
    name = "CBDT - Notifications (via e-filing portal Latest News)"
    keep = ("Notification",)


class CbdtCirculars(_LatestNews):
    id = "cbdt_circulars"
    name = "CBDT - Circulars (via e-filing portal Latest News)"
    keep = ("Circular",)


class CbdtUpdates(_LatestNews):
    id = "cbdt_updates"
    name = "CBDT - Press releases & other updates (via e-filing portal Latest News)"
    keep = ("Press Release", "Update")


SOURCES = (CbdtNotifications, CbdtCirculars, CbdtUpdates)
