"""Reserve Bank of India: notifications, master directions / master circulars, press releases.

RBI's listing pages (NotificationUser.aspx, BS_PressReleaseDisplay.aspx) are ASP.NET pages that show one
month at a time; other months are fetched by posting hdnYear/hdnMonth back with the page's viewstate.
The RSS feeds only carry the latest ~10 items, so they are used as a cheap source of reference numbers.
"""
from __future__ import annotations

import logging
import re
from datetime import date

from bs4 import BeautifulSoup

from .. import http
from ..dates import find_date, parse_date
from .base import Source

log = logging.getLogger(__name__)

BASE = "https://rbi.org.in/Scripts/"
_MONTH_DATE = re.compile(r"^[A-Z][a-z]{2}\s+\d{1,2},\s+\d{4}$")
_REF_START = re.compile(r"^(RBI/|Notification\s+No|No\.|A\.P\.\s*\(DIR|FED\b|DOR\.|DPSS\b|CO\.)", re.I)
_UPDATED = re.compile(r"\(\s*Updated\s+as\s+on\s+([^)]+)\)", re.I)


def _clean(s: str) -> str:
    return " ".join((s or "").split())


def _months_back(since: date, today: date | None = None) -> list[tuple[int, int]]:
    today = today or date.today()
    y, m = today.year, today.month
    out = []
    while (y, m) >= (since.year, since.month):
        out.append((y, m))
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
        if len(out) > 36:
            break
    return out


def _ref_from_html(fragment) -> str:
    """RBI circulars start with 'RBI/2026-27/45 <br> DOR.XXX.REC.12/...' in the first paragraph(s)."""
    for p in fragment.find_all("p", limit=15):
        txt = _clean(p.get_text(" "))
        if not txt:
            continue
        if _REF_START.match(txt) and len(txt) < 220 and not find_date(txt):
            return txt
        if _REF_START.match(txt) and len(txt) < 220:
            # date glued on the same line - cut it off
            m = re.search(r"\s(January|February|March|April|May|June|July|August|September|October|November|December)\s", txt)
            return txt[: m.start()].strip() if m else txt
    return ""


def _parse_listing(html: str, page_url: str):
    """Yield (date, title, url, pdf_url) from an RBI dated listing (date header rows + item rows)."""
    s = BeautifulSoup(html, "lxml")
    cur: date | None = None
    for tr in s.find_all("tr"):
        hdr = tr.find("td", class_="tableheader")
        if hdr is not None and not tr.find("a", class_="link2"):
            txt = _clean(hdr.get_text(" "))
            if _MONTH_DATE.match(txt):
                cur = parse_date(txt)
            continue
        a = tr.find("a", class_="link2")
        if a is None or not a.get("href"):
            continue
        title = _clean(a.get_text(" "))
        pdf = ""
        for pa in tr.find_all("a", href=True):
            if re.search(r"\.pdf($|\?)", pa["href"], re.I):
                pdf = http.absolute(page_url, pa["href"])
                break
        yield cur, title, http.absolute(page_url, a["href"]), pdf


class _MonthlyListing(Source):
    """Shared month-by-month scraper for NotificationUser.aspx / BS_PressReleaseDisplay.aspx."""
    page: str = ""
    rss: str = ""
    doc_type: str = ""

    def _month_pages(self, since: date):
        url = BASE + self.page
        s, r = http.soup(url)
        form = {i["name"]: i.get("value", "") for i in s.select("form input[name]")
                if i.get("type") not in ("submit", "button", "image")}
        for y, m in _months_back(since):
            data = dict(form, hdnYear=str(y), hdnMonth=str(m))
            data["UsrFontCntr$btn"] = ""
            resp = http.post(url, data=data)
            resp.raise_for_status()
            yield resp.text, url

    def _rss_refs(self) -> dict[str, tuple[str, str]]:
        """Map item page URL -> (ref_no, pdf_url) from the RSS feed (best effort)."""
        out: dict[str, tuple[str, str]] = {}
        if not self.rss:
            return out
        try:
            r = http.get(self.rss)
            x = BeautifulSoup(r.content, "xml")
            for it in x.find_all("item"):
                link = _clean(it.link.text if it.link else "")
                desc = it.description.text if it.description else ""
                frag = BeautifulSoup(desc, "lxml")
                pdf = ""
                for a in frag.find_all("a", href=True):
                    if re.search(r"\.pdf($|\?)", a["href"], re.I):
                        pdf = a["href"]
                        break
                out[_key(link)] = (_ref_from_html(frag), pdf)
        except Exception as e:  # noqa: BLE001
            log.debug("RBI RSS failed: %s", e)
        return out

    def fetch(self, since: date):
        refs = self._rss_refs()
        items, seen = [], set()
        for html, page_url in self._month_pages(since):
            for d, title, url, pdf in _parse_listing(html, page_url):
                if d and d < since:
                    continue
                k = _key(url)
                if k in seen:
                    continue
                seen.add(k)
                ref, rss_pdf = refs.get(k, ("", ""))
                items.append(self.item(title=title, date=d, url=url, doc_type=self.doc_type,
                                       ref_no=ref, pdf_url=pdf or rss_pdf))
        return items


def _key(url: str) -> str:
    """Normalise RBI page URLs (host/case vary between RSS and listing)."""
    m = re.search(r"[?&](?:id|prid)=(\d+)", url or "", re.I)
    return m.group(1) if m else (url or "").lower()


class RBINotifications(_MonthlyListing):
    id = "rbi_notifications"
    regulator = "RBI"
    name = "RBI - Notifications (circulars, directions, FEMA)"
    group = "RBI"
    homepage = "https://rbi.org.in/Scripts/NotificationUser.aspx"
    page = "NotificationUser.aspx"
    rss = "https://rbi.org.in/notifications_rss.xml"
    doc_type = "Notification"
    max_detail_fetches = 60

    def fetch(self, since: date):
        items = super().fetch(since)
        # Reference numbers are only on the detail page; fetch them for items the RSS did not cover.
        budget = self.max_detail_fetches
        for it in items:
            if it.ref_no or budget <= 0:
                continue
            budget -= 1
            try:
                s, _ = http.soup(it.url, retries=1)
                it.ref_no = _ref_from_html(s)
            except Exception as e:  # noqa: BLE001
                log.debug("RBI detail %s failed: %s", it.url, e)
        for it in items:
            t = it.title.lower()
            if "master direction" in t:
                it.doc_type = "Master Direction"
            elif re.search(r"\bdirections?,\s*\d{4}", t):
                it.doc_type = "Directions"
            elif "regulations" in t and "foreign exchange management" in t:
                it.doc_type = "FEMA Notification"
        return items


class RBIPressReleases(_MonthlyListing):
    id = "rbi_press_releases"
    enabled_by_default = False  # ~150+/month; most are not compliance updates
    regulator = "RBI"
    name = "RBI - Press Releases"
    group = "RBI"
    homepage = "https://rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx"
    page = "BS_PressReleaseDisplay.aspx"
    rss = ""  # RSS has no ref numbers; listing already has the PDF
    doc_type = "Press Release"


class RBIMaster(Source):
    """Master Directions and Master Circulars. Both pages list everything (grouped by department, then by
    issue date). An MD re-issued with "(Updated as on <date>)" is dated by that update date, since that is
    when it changed."""
    id = "rbi_master"
    regulator = "RBI"
    name = "RBI - Master Directions & Master Circulars"
    group = "RBI"
    homepage = "https://rbi.org.in/Scripts/BS_ViewMasDirections.aspx"
    pages = (
        ("https://rbi.org.in/Scripts/BS_ViewMasDirections.aspx", "Master Direction"),
        ("https://rbi.org.in/Scripts/BS_ViewMasterCirculars.aspx", "Master Circular"),
    )

    def fetch(self, since: date):
        items, seen, errors = [], set(), []
        for url, dtype in self.pages:
            try:
                r = http.get(url)
                r.raise_for_status()
            except Exception as e:  # noqa: BLE001
                errors.append(e)
                continue
            for d, title, link, pdf in _parse_listing(r.text, url):
                m = _UPDATED.search(title)
                upd = find_date(m.group(1)) if m else None
                if upd and (d is None or upd > d):
                    d = upd
                if d and d < since:
                    continue
                k = (link.lower(), d)
                if k in seen:
                    continue
                seen.add(k)
                items.append(self.item(title=title, date=d, url=link, doc_type=dtype, pdf_url=pdf))
        if errors and len(errors) == len(self.pages):
            raise errors[0]
        items.sort(key=lambda i: i.date or date.min, reverse=True)
        return items


SOURCES = (RBINotifications, RBIMaster, RBIPressReleases)
