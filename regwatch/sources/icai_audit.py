"""ICAI announcements & open exposure drafts; NFRA circulars, orders, consultation papers, press briefs."""
from __future__ import annotations

import re
from datetime import date

from .. import http
from ..dates import find_date, parse_date
from .base import Source

ICAI = "https://www.icai.org"
NFRA = "https://nfra.gov.in"  # note: the www. host resets connections; bare domain works


def _clean(s: str) -> str:
    return " ".join((s or "").replace("\xa0", " ").split())


class IcaiAnnouncements(Source):
    id = "icai_announcements"
    regulator = "ICAI"
    name = "ICAI - Announcements"
    group = "ICAI & Audit"
    homepage = f"{ICAI}/category/announcements"
    max_pages = 15

    def fetch(self, since: date):
        items, seen = [], set()
        for page in range(1, self.max_pages + 1):
            url = self.homepage if page == 1 else f"{self.homepage}/{page}"
            b, _ = http.soup(url)
            lis = b.select("ul.list-group li.list-group-item")
            if not lis:
                break
            oldest = None
            for li in lis:
                a = li.find("a", href=True)
                if not a:
                    continue
                text = _clean(a.get_text(" "))
                m = re.search(r"\s*-\s*\((\d{1,2}-\d{1,2}-\d{4})\)\s*$", text)
                d = parse_date(m.group(1)) if m else find_date(text)
                title = text[: m.start()] if m else text
                link = http.absolute(ICAI + "/", a["href"])
                if link in seen:
                    continue
                seen.add(link)
                if d:
                    oldest = d if oldest is None else min(oldest, d)
                if d and d < since:
                    continue
                low = title.lower()
                doc_type = ("Exposure Draft" if "exposure draft" in low else
                            "Announcement")
                items.append(self.item(title=title.rstrip(" ."), date=d, url=link, doc_type=doc_type,
                                       pdf_url=link if link.lower().endswith(".pdf") else ""))
            if oldest and oldest < since:
                break
        return items

    def resolve_pdf(self, item):
        if "icai.org/post/" not in item.url:
            return ""
        b, _ = http.soup(item.url)
        a = b.find("a", href=re.compile(r"\.pdf($|\?)", re.I))
        return http.absolute(item.url, a["href"]) if a else ""


class IcaiOpenExposureDrafts(Source):
    """Board-wise pages listing exposure drafts currently open for comment (undated; title carries the deadline)."""
    id = "icai_exposure_drafts"
    regulator = "ICAI"
    name = "ICAI - Exposure Drafts open for comment"
    group = "ICAI & Audit"
    homepage = f"{ICAI}/category/list-of-exposure-drafts"

    def fetch(self, since: date):
        b, _ = http.soup(self.homepage)
        boards = {}
        for a in b.find_all("a", href=re.compile(r"/post/.*exposure", re.I)):
            boards[http.absolute(ICAI + "/", a["href"])] = _clean(a.get_text(" "))
        items = []
        for url, board_title in boards.items():
            board = re.sub(r"^Exposure Drafts?\s+(of .*?\s+)?issued by\s+(the\s+)?", "", board_title, flags=re.I)
            try:
                pb, _ = http.soup(url)
            except Exception:  # noqa: BLE001 - one bad board page shouldn't drop the rest
                continue
            for tr in pb.find_all("tr"):
                tds = tr.find_all("td")
                a = tr.find("a", href=True)
                if len(tds) < 2 or not a:
                    continue
                title = _clean(a.get_text(" "))
                deadline_txt = _clean(tds[-1].get_text(" "))
                if not title or title.lower().startswith("exposure draft"):
                    continue
                link = http.absolute(url, a["href"])
                deadline = parse_date(deadline_txt)
                if deadline and deadline < date.today():
                    continue
                items.append(self.item(
                    title=f"{title} (comments by {deadline_txt})" if deadline_txt else title,
                    date=None, url=link, doc_type="Exposure Draft", ref_no=board,
                    pdf_url=link if link.lower().endswith(".pdf") else ""))
        return items


class _NfraCategory(Source):
    regulator = "NFRA"
    group = "ICAI & Audit"
    slug = ""
    doc_type = ""
    max_pages = 5

    @property
    def homepage(self) -> str:  # type: ignore[override]
        return f"{NFRA}/document-category/{self.slug}/"

    def fetch(self, since: date):
        items, seen = [], set()
        for page in range(1, self.max_pages + 1):
            url = self.homepage if page == 1 else f"{self.homepage}page/{page}/"
            try:
                b, _ = http.soup(url)
            except Exception:
                if page == 1:
                    raise
                break
            rows = [tr for tr in b.find_all("tr") if tr.find("td")]
            if not rows:
                break
            oldest = None
            for tr in rows:
                tds = tr.find_all("td")
                title = _clean(tds[0].get_text(" "))
                d = None
                for td in tds[1:]:
                    d = parse_date(_clean(td.get_text(" ")))
                    if d:
                        break
                pdf = next((a["href"] for a in tr.find_all("a", href=True) if ".pdf" in a["href"].lower()), "")
                link = http.absolute(url, pdf or next((a["href"] for a in tr.find_all("a", href=True)), url))
                key = (title, link)
                if not title or key in seen:
                    continue
                seen.add(key)
                if d:
                    oldest = d if oldest is None else min(oldest, d)
                if d and d < since:
                    continue
                items.append(self.item(title=title, date=d, url=link, doc_type=self.doc_type,
                                       pdf_url=link if pdf else ""))
            if (oldest and oldest < since) or not b.find("a", href=re.compile(rf"/page/{page + 1}/?$")):
                break
        return items


class NfraCirculars(_NfraCategory):
    id, name, slug, doc_type = "nfra_circulars", "NFRA - Circulars", "circulars", "Circular"


class NfraOrders(_NfraCategory):
    id, name, slug, doc_type = "nfra_orders", "NFRA - Orders", "orders", "Order"


class NfraConsultation(_NfraCategory):
    id, name, slug, doc_type = "nfra_consultation", "NFRA - Consultation Papers", "consultation-papers", "Consultation Paper"


class NfraInspection(_NfraCategory):
    id, name, slug, doc_type = "nfra_inspection", "NFRA - Inspection / AQR Reports", "inspection-reports", "Inspection Report"


class NfraPress(_NfraCategory):
    id, name, slug, doc_type = "nfra_press", "NFRA - Press Briefs", "press-release", "Press Release"


SOURCES = (IcaiAnnouncements, IcaiOpenExposureDrafts, NfraCirculars, NfraOrders, NfraConsultation, NfraInspection, NfraPress)
