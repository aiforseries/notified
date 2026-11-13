"""IBBI - regulations (consolidated, as amended), circulars, notifications, guidelines,
discussion papers (public comments) and press releases.

All listings are plain server-rendered tables (Date | Subject | PDF), newest first,
paginated with ?page=N (20 rows per page).
"""
from __future__ import annotations

import re
from datetime import date

from .. import http
from ..dates import find_date, parse_date
from .base import Source

IBBI = "https://ibbi.gov.in"
_SIZE_RX = re.compile(r"\(?\s*\d[\d,.]*\s*(?:KB|MB|GB|bytes)\s*\)?", re.I)
_ONCLICK_RX = re.compile(r"""newwindow1\(\s*['"]([^'"]+)['"]""")


def _clean(s: str) -> str:
    s = " ".join((s or "").replace("\xa0", " ").split())
    s = _SIZE_RX.sub("", s)
    s = re.sub(r"\s+PDF\s*$", "", s)
    return " ".join(s.split()).strip(" -|")


def _date(s: str):
    # dates.parse_date turns "September" into "Sepember" (its Sept->Sep fix); pre-shorten it here.
    return parse_date(re.sub(r"\bSept(ember)?\b", "Sep", s or "", flags=re.I))


def _abs(href: str) -> str:
    return re.sub(r"(?<!:)/{2,}", "/", http.absolute(IBBI + "/", href))


def _pdf_in(tag) -> str:
    for a in tag.find_all("a"):
        href = (a.get("href") or "").strip()
        if href.lower().split("?")[0].endswith(".pdf"):
            return _abs(href)
        m = _ONCLICK_RX.search(a.get("onclick") or "")
        if m:
            return _abs(m.group(1))
    return ""


class _IbbiTable(Source):
    regulator = "IBBI"
    group = "Insolvency & Valuation"
    path = ""
    doc_type = ""
    max_pages = 10

    @property
    def homepage(self) -> str:  # type: ignore[override]
        return f"{IBBI}/{self.path}"

    def fetch(self, since: date):
        items, seen = [], set()
        for page in range(1, self.max_pages + 1):
            url = self.homepage if page == 1 else f"{self.homepage}?page={page}"
            try:
                b, _ = http.soup(url)
            except Exception:
                if page == 1:
                    raise
                break
            rows = [tr for tr in b.find_all("tr") if tr.find("td")]
            if not rows:
                if page == 1:
                    raise RuntimeError(f"IBBI listing has no table rows: {url}")
                break
            oldest, new = None, 0
            for tr in rows:
                tds = tr.find_all("td")
                d, di = None, -1
                for i, td in enumerate(tds):
                    d = _date(_clean(td.get_text(" ")))
                    if d:
                        di = i
                        break
                # subject = longest remaining cell text
                cand = [_clean(td.get_text(" ")) for i, td in enumerate(tds) if i != di]
                cand = [c for c in cand if c and not c.isdigit()]
                title = max(cand, key=len) if cand else ""
                if not title or len(title) < 8:
                    continue
                if d is None:
                    d = find_date(title) if di < 0 else None
                pdf = _pdf_in(tr)
                key = (title.lower(), pdf)
                if key in seen:
                    continue
                seen.add(key)
                new += 1
                if d:
                    oldest = d if oldest is None else min(oldest, d)
                if d and d < since:
                    continue
                items.append(self.item(title=title.rstrip(" ."), date=d, url=pdf or url,
                                       doc_type=self.doc_type, pdf_url=pdf))
            if not new or (oldest and oldest < since):
                break
            if not b.find("a", href=re.compile(rf"[?&]page={page + 1}\b")):
                break
        return items


class IbbiRegulations(_IbbiTable):
    """Consolidated regulations re-published each time they are amended ("Amended upto dd-mm-yyyy")."""
    id, name, path, doc_type = "ibbi_regulations", "IBBI - Regulations (as amended)", "legal-framework/updated", "Regulation"


class IbbiCirculars(_IbbiTable):
    id, name, path, doc_type = "ibbi_circulars", "IBBI - Circulars", "legal-framework/circulars", "Circular"


class IbbiNotifications(_IbbiTable):
    id, name, path, doc_type = "ibbi_notifications", "IBBI - Notifications", "legal-framework/notifications", "Notification"


class IbbiGuidelines(_IbbiTable):
    id, name, path, doc_type = "ibbi_guidelines", "IBBI - Guidelines", "legal-framework/guidelines", "Guideline"


class IbbiDiscussionPapers(_IbbiTable):
    id, name, path, doc_type = ("ibbi_discussion_papers", "IBBI - Discussion Papers (public comments)",
                                "public-comments/comments-on", "Discussion Paper")
    max_pages = 1  # single long page


class IbbiPressReleases(_IbbiTable):
    id, name, path, doc_type = "ibbi_press_releases", "IBBI - Press Releases", "media/press-releases", "Press Release"


SOURCES = (IbbiRegulations, IbbiCirculars, IbbiNotifications, IbbiGuidelines, IbbiDiscussionPapers, IbbiPressReleases)
