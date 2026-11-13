"""PFRDA - circulars, master circulars, regulations, notifications, guidelines.

pfrda.org.in is a Liferay site; each /regulatory-framework/<section> page is a server-rendered
search-results portlet (cards with title, tag, "Ref:" and "Issue Date:"), newest first,
paged with ?delta=N&start=<page>. Cards link to a /w/<slug> detail page holding the PDF.
"""
from __future__ import annotations

import re
from datetime import date

from .. import http
from ..dates import parse_date
from .base import Source

PFRDA = "https://www.pfrda.org.in"
DELTA = 20
MAX_PAGES = 10


def _clean(s: str) -> str:
    return " ".join((s or "").replace("\xa0", " ").split())


class _PfrdaListing(Source):
    regulator = "PFRDA"
    group = "Insurance & Pension"
    path = ""
    doc_type = ""

    @property
    def homepage(self) -> str:  # type: ignore[override]
        return f"{PFRDA}/regulatory-framework/{self.path}"

    def fetch(self, since: date):
        items, seen = [], set()
        for page in range(1, MAX_PAGES + 1):
            b, _ = http.soup(self.homepage, params={"delta": str(DELTA), "start": str(page)})
            cards = b.select("div.basic-card")
            if not cards:
                if page == 1:
                    raise RuntimeError(f"PFRDA {self.path}: no listing cards found (page layout changed?)")
                break
            new, oldest = 0, None
            for c in cards:
                a = c.find("a", href=True)
                t = c.select_one(".basic-title")
                title = _clean(t.get_text(" ") if t else (a.get_text(" ") if a else ""))
                if not a or not title:
                    continue
                link = http.absolute(PFRDA + "/", a["href"]).split("?p_l_back_url")[0]
                if link in seen:
                    continue
                seen.add(link)
                new += 1
                ref, d = "", None
                for m in c.select(".meta-item"):
                    txt = _clean(m.get_text(" "))
                    if txt.lower().startswith("ref"):
                        ref = re.sub(r"^Ref\s*:?\s*", "", txt, flags=re.I)
                    elif "date" in txt.lower():
                        d = parse_date(re.sub(r"^.*?Date\s*:?\s*", "", txt, flags=re.I))
                if d:
                    oldest = d if oldest is None else min(oldest, d)
                if d and d < since:
                    continue
                items.append(self.item(title=title.rstrip(" ."), date=d, url=link, doc_type=self.doc_type, ref_no=ref,
                                       pdf_url=link if link.lower().endswith(".pdf") else ""))
            if not new or (oldest and oldest < since):
                break
        return items

    def resolve_pdf(self, item):
        if "/w/" not in item.url:
            return ""
        page = http.text(http.get(item.url))
        m = re.search(r"""href=["']([^"']*/documents/\d+/\d+/[^"']+?\.pdf[^"']*)["']""", page, re.I)
        return http.absolute(item.url, m.group(1).replace("&amp;", "&")) if m else ""


class PfrdaCirculars(_PfrdaListing):
    id, name, path, doc_type = "pfrda_circulars", "PFRDA - Circulars", "circulars/active-circulars", "Circular"


class PfrdaMasterCirculars(_PfrdaListing):
    id, name, path, doc_type = ("pfrda_master_circulars", "PFRDA - Master Circulars",
                                "master-circulars/active-master-circulars", "Master Circular")


class PfrdaRegulations(_PfrdaListing):
    id, name, path, doc_type = "pfrda_regulations", "PFRDA - Regulations", "regulations", "Regulation"


class PfrdaNotifications(_PfrdaListing):
    id, name, path, doc_type = "pfrda_notifications", "PFRDA - Notifications", "notifications", "Notification"


class PfrdaGuidelines(_PfrdaListing):
    id, name, path, doc_type = "pfrda_guidelines", "PFRDA - Guidelines", "guidelines", "Guideline"


SOURCES = (PfrdaCirculars, PfrdaMasterCirculars, PfrdaRegulations, PfrdaNotifications, PfrdaGuidelines)
