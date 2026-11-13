"""SEBI - legal listings (circulars, master circulars, regulations, gazette notifications) and press releases.

Uses the same AJAX endpoint the public listing page uses, with its own from/to date filter.
"""
from __future__ import annotations

import re
from datetime import date

from bs4 import BeautifulSoup

from .. import http
from ..dates import parse_date
from .base import Source

AJAX = "https://www.sebi.gov.in/sebiweb/ajax/home/getnewslistinfo.jsp"
LISTING = "https://www.sebi.gov.in/sebiweb/home/HomeAction.do?doListing=yes&sid={sid}&ssid={ssid}&smid=0"
MAX_PAGES = 20


class _SebiListing(Source):
    regulator = "SEBI"
    group = "SEBI"
    sid = "1"
    ssid = ""
    s_text = "Legal"
    ss_text = ""
    doc_type = ""

    @property
    def homepage(self) -> str:  # type: ignore[override]
        return LISTING.format(sid=self.sid, ssid=self.ssid)

    def _page(self, since: date, page: int) -> str:
        data = dict(
            nextValue="1", next="s" if page == 0 else "n", search="",
            fromDate=since.strftime("%d-%m-%Y"), toDate=date.today().strftime("%d-%m-%Y"),
            fromYear="", toYear="", deptId="", sid=self.sid, ssid=self.ssid, smid="0",
            ssidhidden=self.ssid, intmid="-1", sText=self.s_text, ssText=self.ss_text, smText="",
            doDirect="-1" if page == 0 else str(page),
        )
        r = http.post(AJAX, data=data, headers={"X-Requested-With": "XMLHttpRequest", "Referer": self.homepage})
        r.raise_for_status()
        return http.text(r)

    def fetch(self, since: date):
        items, seen = [], set()
        total = None
        for page in range(MAX_PAGES):
            html = self._page(since, page)
            if total is None:
                m = re.search(r"of\s+(\d+)\s+records", html)
                total = int(m.group(1)) if m else None
            b = BeautifulSoup(html, "lxml")
            new = 0
            for tr in b.find_all("tr"):
                tds = tr.find_all("td")
                a = tr.find("a", href=True)
                if len(tds) < 2 or not a:
                    continue
                url = http.absolute("https://www.sebi.gov.in/", a["href"])
                if url in seen:
                    continue
                seen.add(url)
                new += 1
                d = parse_date(tds[0].get_text(" ", strip=True))
                if d and d < since:
                    continue
                title = (a.get("title") or a.get_text(" ", strip=True)).strip()
                th = tr.find("th")
                ref = th.get_text(" ", strip=True) if th else ""
                items.append(self.item(title=title, date=d, url=url, doc_type=self.doc_type, ref_no=ref,
                                       pdf_url=url if url.lower().endswith(".pdf") else ""))
            if not new or (total is not None and len(seen) >= total):
                break
        return items

    def resolve_pdf(self, item):
        page = http.text(http.get(item.url))
        m = (re.search(r"""(?:src|href)=['"][^'"]*?file=(https?://[^'"]+?\.pdf)""", page, re.I)
             or re.search(r"""(?:src|href)=['"](https?://[^'"]+?\.pdf)['"]""", page, re.I))
        return m.group(1) if m else ""


class SebiCirculars(_SebiListing):
    id, name, ssid, ss_text, doc_type = "sebi_circulars", "SEBI - Circulars", "7", "Circulars", "Circular"


class SebiMasterCirculars(_SebiListing):
    id, name, ssid, ss_text, doc_type = "sebi_master_circulars", "SEBI - Master Circulars", "6", "Master Circulars", "Master Circular"


class SebiRegulations(_SebiListing):
    id, name, ssid, ss_text, doc_type = "sebi_regulations", "SEBI - Regulations / Amendments", "3", "Regulations", "Regulation"


class SebiGazette(_SebiListing):
    id, name, ssid, ss_text, doc_type = "sebi_gazette", "SEBI - Gazette Notifications", "82", "Gazette Notification", "Notification"


class SebiPressReleases(_SebiListing):
    id, name, sid, ssid, s_text, ss_text, doc_type = (
        "sebi_press_releases", "SEBI - Press Releases", "6", "23", "Media & Notifications", "Press Releases", "Press Release")


SOURCES = (SebiCirculars, SebiMasterCirculars, SebiRegulations, SebiGazette, SebiPressReleases)
