"""IRDAI - circulars, regulations (consolidated & gazette notified), notifications, guidelines.

irdai.gov.in is a Liferay portal; each listing is a server-rendered search container
(IRDAIDocumentMediaPortlet) sorted newest-first by "Last Updated" and paged with
`_<portlet>_cur=N`. Paging only works on the canonical page paths used below
(e.g. /regulations2 ignores `cur`). Rows carry direct document-library PDF links.
Titles are bilingual ("Hindi / English"); the English part is kept when present.
"""
from __future__ import annotations

import re
from datetime import date

from .. import http
from ..dates import parse_date
from .base import Source

IRDAI = "https://irdai.gov.in"
PORTLET = "com_irdai_document_media_IRDAIDocumentMediaPortlet"
DELTA = 20
MAX_PAGES = 10

_DEVANAGARI = re.compile(r"[ऀ-ॿ]")


def _clean(s: str) -> str:
    return " ".join((s or "").replace("\xa0", " ").split())


def _english(s: str) -> str:
    """'हिंदी शीर्षक / English title' -> 'English title' (falls back to the full text)."""
    s = _clean(s)
    if not _DEVANAGARI.search(s):
        return s
    parts = [p.strip() for p in re.split(r"\s+/\s+|\s+_\s+", s) if p.strip()]
    eng = [p for p in parts if not _DEVANAGARI.search(p)]
    if eng:
        return " / ".join(eng)
    # mixed run like "हिंदी ... Gazette Notification on X": cut at the first Latin word run
    m = re.search(r"[A-Za-z][A-Za-z0-9 ,.()&'’\-:;/]{15,}$", s)
    return m.group(0).strip() if m else s


class _IrdaiListing(Source):
    regulator = "IRDAI"
    group = "Insurance & Pension"
    path = ""
    doc_type = ""

    @property
    def homepage(self) -> str:  # type: ignore[override]
        return f"{IRDAI}/{self.path}"

    def _page(self, cur: int):
        params = {
            "p_p_id": PORTLET, "p_p_lifecycle": "0", "p_p_state": "normal", "p_p_mode": "view",
            f"_{PORTLET}_delta": str(DELTA), f"_{PORTLET}_resetCur": "false", f"_{PORTLET}_cur": str(cur),
        }
        b, _ = http.soup(self.homepage, params=params if cur > 1 else None)
        return b

    def fetch(self, since: date):
        items, seen = [], set()
        for cur in range(1, MAX_PAGES + 1):
            b = self._page(cur)
            tbody = b.find("tbody", class_="table-data")
            if tbody is None:
                if cur == 1:
                    raise RuntimeError(f"IRDAI {self.path}: listing table not found (page layout changed?)")
                break
            heads = [_clean(th.get_text(" ")).lower() for th in tbody.find_parent("table").find_all("th")]

            def ci(*names):
                return next((i for i, h in enumerate(heads) if any(n in h for n in names)), None)

            i_title, i_sub, i_date, i_ref = ci("short description"), ci("sub title"), ci("last updated"), ci("reference")
            new, oldest = 0, None
            for tr in tbody.find_all("tr", recursive=False):
                tds = tr.find_all("td", recursive=False)
                if not tds:
                    continue

                def col(i):
                    return _clean(tds[i].get_text(" ")) if i is not None and i < len(tds) else ""

                detail = next((a["href"] for a in tr.find_all("a", href=True) if "document-detail" in a["href"]), "")
                pdf = next((a["href"] for a in tr.find_all("a", href=True) if "/documents/" in a["href"]), "")
                link = http.absolute(IRDAI + "/", detail or pdf)
                title = _english(col(i_title))
                if not title or not link or link in seen:
                    continue
                seen.add(link)
                new += 1
                d = parse_date(col(i_date))
                if d:
                    oldest = d if oldest is None else min(oldest, d)
                if d and d < since:
                    continue
                ref = col(i_ref)
                sub = _english(col(i_sub))
                if ref and (ref.lower() == title.lower() or not re.search(r"\d", ref)):
                    ref = ""  # column often just repeats the title or the doc type
                doc_type = self.doc_type
                if "master circular" in sub.lower():
                    doc_type = "Master Circular"
                elif "gazette" in sub.lower():
                    doc_type = "Gazette Notification"
                items.append(self.item(title=title.rstrip(" ."), date=d, url=link, doc_type=doc_type, ref_no=ref,
                                       pdf_url=http.absolute(IRDAI + "/", pdf) if pdf else ""))
            if not new or (oldest and oldest < since):
                break
        return items

    def resolve_pdf(self, item):
        if "document-detail" not in item.url:
            return ""
        b, _ = http.soup(item.url)
        for a in b.find_all("a", href=re.compile(r"/documents/\d+/")):
            if "policyholder+complaints" not in a["href"].lower():  # site-wide footer form
                return http.absolute(item.url, a["href"])
        return ""


class IrdaiCirculars(_IrdaiListing):
    id, name, path, doc_type = "irdai_circulars", "IRDAI - Circulars", "circulars", "Circular"


class IrdaiRegulations(_IrdaiListing):
    id, name, path, doc_type = ("irdai_regulations", "IRDAI - Regulations (Gazette notified / consolidated)",
                                "consolidated-gazette-notified-regulations", "Regulation")


class IrdaiNotifications(_IrdaiListing):
    id, name, path, doc_type = "irdai_notifications", "IRDAI - Notifications", "notifications", "Notification"


class IrdaiGuidelines(_IrdaiListing):
    id, name, path, doc_type = "irdai_guidelines", "IRDAI - Guidelines", "guidelines", "Guideline"


SOURCES = (IrdaiCirculars, IrdaiRegulations, IrdaiNotifications, IrdaiGuidelines)
