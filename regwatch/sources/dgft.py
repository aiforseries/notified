"""DGFT (Foreign Trade Policy) - notifications, public notices, trade notices and policy circulars.

Each dgft.gov.in/CP/?opt=<kind> page is server-rendered with the full multi-year table
(Number | Year | Description | Date | CRT DT | Attachment), so one GET per feed is enough.
Attachments are direct PDF links on content.dgft.gov.in.
"""
from __future__ import annotations

from datetime import date
from urllib.parse import quote

from .. import http
from ..dates import parse_date
from .base import Source

DGFT = "https://www.dgft.gov.in"


def _clean(s: str) -> str:
    return " ".join((s or "").replace("\xa0", " ").replace("�", "-").split())


def _safe_url(u: str) -> str:
    # attachment links contain raw spaces ("Notification No. 49 of 2026-2027.pdf")
    return quote(u.strip(), safe=":/?&=%#+,;@!$'()*~")


class _DgftListing(Source):
    regulator = "DGFT"
    group = "GST & Customs"
    opt = ""
    doc_type = ""
    label = ""  # prefix for ref_no, e.g. "Notification No."

    @property
    def homepage(self) -> str:  # type: ignore[override]
        return f"{DGFT}/CP/?opt={self.opt}"

    def fetch(self, since: date):
        b, _ = http.soup(self.homepage)
        table = None
        for t in b.find_all("table"):
            heads = [_clean(th.get_text(" ")).lower() for th in t.find_all("th")]
            if "description" in heads and "date" in heads:
                table, cols = t, heads
                break
        if table is None:
            raise RuntimeError(f"DGFT {self.opt}: listing table not found (page layout changed?)")
        ix = {name: cols.index(name) for name in ("number", "year", "description", "date") if name in cols}

        items, seen = [], set()
        for tr in table.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) < len(ix) or "description" not in ix:
                continue

            def col(name: str) -> str:
                i = ix.get(name)
                return _clean(tds[i].get_text(" ")) if i is not None and i < len(tds) else ""

            title = col("description")
            if not title:
                continue
            d = parse_date(col("date"))
            if d and d < since:
                continue
            num, year = col("number"), col("year")
            if num and "/" not in num and year:
                num = f"{num}/{year}"
            a = next((a for a in tr.find_all("a", href=True) if a["href"].strip() and not a["href"].startswith("#")), None)
            link = _safe_url(http.absolute(DGFT + "/", a["href"])) if a else self.homepage
            pdf = link if a and (".pdf" in link.lower() or "pdf" in _clean(a.get_text(" ")).lower()) else ""
            key = (num, title, link)
            if key in seen:
                continue
            seen.add(key)
            items.append(self.item(title=title.rstrip(" ."), date=d, url=link, doc_type=self.doc_type,
                                   ref_no=f"{self.label} {num}".strip() if num else "", pdf_url=pdf))
        return items


class DgftNotifications(_DgftListing):
    id, name, opt, doc_type, label = "dgft_notifications", "DGFT - Notifications", "notification", "Notification", "Notification No."


class DgftPublicNotices(_DgftListing):
    id, name, opt, doc_type, label = "dgft_public_notices", "DGFT - Public Notices", "public-notice", "Public Notice", "Public Notice No."


class DgftTradeNotices(_DgftListing):
    id, name, opt, doc_type, label = "dgft_trade_notices", "DGFT - Trade Notices", "trade-notice", "Trade Notice", "Trade Notice No."


class DgftCirculars(_DgftListing):
    id, name, opt, doc_type, label = "dgft_circulars", "DGFT - Policy Circulars", "circular", "Circular", "Policy Circular No."


SOURCES = (DgftNotifications, DgftPublicNotices, DgftTradeNotices, DgftCirculars)
