"""ICSI - latest updates (exam/student items filtered out), ICSI Standards page
(secretarial / auditing standards, guidance notes) and press releases.

icsi.edu is a JS app backed by a same-origin JSON CMS API (/api/...), which we read directly.
"""
from __future__ import annotations

import json
import re
from datetime import date
from urllib.parse import parse_qs, urlparse

from .. import http
from ..dates import parse_date
from .base import Source

ICSI = "https://www.icsi.edu"
_H = {"Accept": "application/json"}

# Pure student / examination notices - not relevant to practising professionals.
_STUDENT_RX = re.compile(
    r"\b(cseet|examination|exam|answer\s*book|admit\s*card|e-admit|study\s*material|result|toppers?|"
    r"rank\s*holders?|verification\s*of\s*marks|students?|question\s*papers?|registration\s*for\s*executive|"
    r"general\s*observers?|examiners?|scholarship)\b", re.I)


def _clean(s) -> str:
    return " ".join(str(s or "").replace("\xa0", " ").replace("​", "").split())


def _api(path: str):
    r = http.get(ICSI + path, headers=_H)
    r.raise_for_status()
    return json.loads(http.text(r))


def _links(url: str) -> tuple[str, str]:
    """Return (page_url, pdf_url). Unwrap the site's /pdf-viewer?src=<pdf> wrapper."""
    url = http.absolute(ICSI + "/", url or "")
    p = urlparse(url)
    if p.path.rstrip("/").endswith("pdf-viewer"):
        src = (parse_qs(p.query).get("src") or [""])[0]
        if src:
            return url, src
    return url, (url if p.path.lower().endswith(".pdf") else "")


class IcsiLatestUpdates(Source):
    id = "icsi_latest_updates"
    regulator = "ICSI"
    name = "ICSI - Latest Updates (members)"
    group = "Corporate Law (ICSI/CCI)"
    homepage = f"{ICSI}/latest-updates"

    def fetch(self, since: date):
        data = _api("/api/home/latest-updates/")
        rows = ((data.get("announcements") or {}).get("items")) or []
        items = []
        for x in rows:
            title = _clean(x.get("text"))
            if not title or _STUDENT_RX.search(title):
                continue
            d = parse_date(_clean(x.get("date")))
            if d and d < since:
                continue
            url, pdf = _links(x.get("url"))
            low = title.lower()
            doc_type = "Circular" if "circular" in low else "Notification" if "notification" in low else "Announcement"
            items.append(self.item(title=title, date=d, url=url, doc_type=doc_type, pdf_url=pdf))
        return items


class _IcsiDocPage(Source):
    """A CMS page made of (optionally tabbed) document lists. Dates are the CMS upload dates."""
    regulator = "ICSI"
    group = "Corporate Law (ICSI/CCI)"
    slug = ""
    doc_type = ""

    @property
    def homepage(self) -> str:  # type: ignore[override]
        return f"{ICSI}/{self.slug}"

    def _walk(self, blocks, label, out):
        for b in blocks or []:
            t, v = b.get("type"), b.get("value")
            if t == "tabs" and isinstance(v, dict):
                for tab in v.get("tabs") or []:
                    self._walk(tab.get("blocks"), _clean(tab.get("label")) or label, out)
            elif t == "document" and isinstance(v, dict):
                for doc in v.get("docs") or []:
                    out.append((label, doc))
            elif isinstance(v, dict) and isinstance(v.get("blocks"), list):
                self._walk(v["blocks"], label, out)

    def fetch(self, since: date):
        page = _api(f"/api/pages/{self.slug}/")
        docs: list = []
        self._walk(page.get("blocks"), "", docs)
        if not docs:
            raise RuntimeError(f"ICSI page '{self.slug}' has no document blocks")
        items, seen = [], set()
        for label, doc in docs:
            title = _clean(doc.get("title"))
            url, pdf = _links(doc.get("url"))
            if not title or (title, url) in seen:
                continue
            seen.add((title, url))
            d = parse_date(_clean(doc.get("date")))
            if d and d < since:
                continue
            low = title.lower()
            doc_type = ("Guidance Note" if "guidance" in low else "FAQ" if "faq" in low else self.doc_type)
            items.append(self.item(title=title, date=d, url=url, doc_type=doc_type, ref_no=label, pdf_url=pdf))
        return items


class IcsiStandards(_IcsiDocPage):
    id, name, slug, doc_type = "icsi_standards", "ICSI - Secretarial & Auditing Standards", "icsi-standards", "Standard"


class IcsiPressReleases(Source):
    id = "icsi_press_releases"
    regulator = "ICSI"
    name = "ICSI - Press Releases"
    group = "Corporate Law (ICSI/CCI)"
    homepage = f"{ICSI}/media-centre/press-realese"

    def fetch(self, since: date):
        data = _api("/api/media-centre/press-releases/")
        rows = data.get("releases")
        if rows is None:
            raise RuntimeError("ICSI press-release API returned an unexpected payload")
        items = []
        for x in rows:
            title = _clean(x.get("title"))
            if not title:
                continue
            d = parse_date(_clean((x.get("meta") or {}).get("date")))
            if d and d < since:
                continue
            url, pdf = _links(x.get("url"))
            items.append(self.item(title=title, date=d, url=url, doc_type="Press Release", pdf_url=pdf))
        return items


SOURCES = (IcsiLatestUpdates, IcsiStandards, IcsiPressReleases)
