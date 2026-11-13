"""CERT-In (Indian Computer Emergency Response Team): advisories, and directions & guidelines.

CERT-In's server is slow and drops rapid repeated requests, so each source makes one or two requests
with a long timeout. Vulnerability notes (software patch alerts) are left out: they are high-volume and
technical, not regulatory."""
from __future__ import annotations

import re
from datetime import date

from bs4 import BeautifulSoup

from .. import http
from ..dates import find_date
from .base import Source

BASE = "https://www.cert-in.org.in"
TIMEOUT = 45  # one attempt only: a slow or throttling server must not hold up the whole fetch


def _clean(s: str) -> str:
    return " ".join((s or "").replace("\xa0", " ").split())


class CertInAdvisories(Source):
    id = "certin_advisories"
    regulator = "CERT-In"
    name = "CERT-In - Advisories"
    group = "Data Protection"
    homepage = f"{BASE}/s2cMainServlet?pageid=PUBADVLIST"

    def _year(self, year: int) -> list:
        r = http.get(f"{BASE}/s2cMainServlet?pageid=PUBADVLIST02&year={year}", timeout=TIMEOUT, retries=0)
        r.raise_for_status()
        b = BeautifulSoup(http.text(r), "lxml")
        out = []
        for a in b.find_all("a", href=re.compile(r"VLCODE=CIAD-\d{4}-\d+")):
            code = re.search(r"CIAD-\d{4}-\d+", a["href"]).group(0)
            row = a.find_parent("tr")
            date_row = row.find_next_sibling("tr") if row else None
            title_row = date_row.find_next_sibling("tr") if date_row else None
            d = find_date(date_row.get_text(" ")) if date_row else None
            title = _clean(title_row.get_text(" ")) if title_row else ""
            out.append((code, d, title or f"CERT-In Advisory {code}", http.absolute(BASE + "/", a["href"])))
        return out

    def fetch(self, since: date):
        rows = []
        for year in range(date.today().year, since.year - 1, -1):
            got = self._year(year)
            if not got and year == date.today().year:
                raise RuntimeError("no advisories found on CERT-In's list (layout changed?)")
            rows += got
        return [self.item(title=t, date=d, url=u, doc_type="Advisory", ref_no=code)
                for code, d, t, u in rows if not d or d >= since]


class CertInGuidelines(Source):
    """Directions under section 70B of the IT Act and CERT-In's guidelines (published as PDFs, undated)."""
    id = "certin_guidelines"
    regulator = "CERT-In"
    name = "CERT-In - Directions & guidelines"
    group = "Data Protection"
    homepage = f"{BASE}/Directions70B.jsp"
    undated_baseline = True

    def fetch(self, since: date):
        r = http.get(self.homepage, timeout=TIMEOUT, retries=0)
        r.raise_for_status()
        b = BeautifulSoup(http.text(r), "lxml")
        items, seen = [], set()
        for a in b.find_all("a", href=re.compile(r"\.pdf($|\?)", re.I)):
            title = _clean(a.get_text(" "))
            if len(title) < 8 or title.upper().startswith("RFC") or title in seen:
                continue
            seen.add(title)
            url = http.absolute(BASE + "/", a["href"])
            d = find_date(title)
            if d and d < since:
                continue
            items.append(self.item(title=title, date=d, url=url, pdf_url=url,
                                   doc_type="Directions" if "direction" in title.lower() else "Guidelines"))
        if not items and not seen:
            raise RuntimeError("no documents found on CERT-In's directions page (layout changed?)")
        return items


SOURCES = (CertInAdvisories, CertInGuidelines)
