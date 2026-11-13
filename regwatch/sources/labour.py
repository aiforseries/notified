"""Labour: Ministry of Labour & Employment (labour.gov.in), EPFO, ESIC.

* labour.gov.in is a Next.js front-end over WordPress; its REST API (/cms/wp-json/wp/v2/...) is public for the
  content types we need: `central_documents` (main document store - Labour Code rules/notifications, FAQs,
  orders), `documents` (category 146 = Gazette Notifications, 144 = Press Release) and `whats_new`.
  The issue date is in ACF fields (`file_date` / `date`, YYYYMMDD); attachments are resolved via /wp/v2/media.
* EPFO moved to www.epfo.gov.in (WordPress). /circulars/ is one big page of "download-box" cards (it has not been
  updated since early 2026); new circulars now appear as "notices", which have a WordPress RSS feed.
* ESIC /circulars is a paginated table (10 rows/page): Branch | Circular No. & date | Subject (PDF links) |
  Publish date | Console No.
"""
from __future__ import annotations

import html as _html
import logging
import re
from datetime import date, datetime

from bs4 import BeautifulSoup

from .. import http
from ..dates import find_date, parse_date
from .base import Source

log = logging.getLogger(__name__)


def _clean(s: str) -> str:
    return " ".join(_html.unescape(s or "").split())


def _ymd(s) -> date | None:
    s = str(s or "").strip()
    if re.fullmatch(r"\d{8}", s):
        try:
            return datetime.strptime(s, "%Y%m%d").date()
        except ValueError:
            return None
    return parse_date(s)


def _mostly_devanagari(s: str) -> bool:
    dev = sum(1 for c in s if "ऀ" <= c <= "ॿ")
    lat = sum(1 for c in s if c.isascii() and c.isalpha())
    return dev > lat


_REF_RX = re.compile(
    r"\b(?:Circular\s+|Instn\.?\s+|O\.?O\.?\s+|File\s+)?No\.?\s*[:.]?\s*([A-Za-z0-9][\w\-/().&, ]{3,90}?)\s+(?:dated|dt\.?)\b",
    re.I,
)


def _ref_from_title(title: str) -> str:
    m = _REF_RX.search(title or "")
    return _clean(m.group(1)).rstrip(" ,-") if m else ""


# ----------------------------------------------------------------------------------------- MoLE

_MOLE_API = "https://www.labour.gov.in/cms/wp-json/wp/v2/"
_MOLE_NOISE = re.compile(
    r"\b(promotion|apars?|engagement of retired|consultants?\b|eoi|expression of interest|tender|vacanc|recruit|"
    r"deputation|seniority|posting|upsc|who.?s who|immovable property|probationary|vigilance status|"
    r"demand for grants|advertisement)", re.I)


class _MoLEBase(Source):
    regulator = "MoLE"
    group = "Labour"
    per_page = 50
    max_pages = 10

    def _collect(self, ptype: str, since: date, params: dict | None = None) -> list[dict]:
        """Page through a WP collection (newest post first) until posts are older than `since`.
        Post date is >= issue date, so stopping on post date never drops an in-range item."""
        out: list[dict] = []
        for page in range(1, self.max_pages + 1):
            q = {"per_page": self.per_page, "page": page, "orderby": "date", "order": "desc",
                 "_fields": "id,date,link,title,acf,documents_category,cd_category"}
            q.update(params or {})
            r = http.get(_MOLE_API + ptype, params=q)
            if r.status_code == 400 and page > 1:  # past the last page
                break
            r.raise_for_status()
            rows = r.json()
            if not isinstance(rows, list) or not rows:
                break
            out.extend(rows)
            last = (rows[-1].get("date") or "")[:10]
            if len(rows) < self.per_page or (last and _ymd(last) and _ymd(last) < since):
                break
        return out

    @staticmethod
    def _media_ids(acf: dict) -> list[int]:
        ids: list[int] = []
        for k in ("pdf", "pdf_both", "pdf_hindi"):  # English first
            v = acf.get(k)
            if isinstance(v, int):
                ids.append(v)
        f = acf.get("file")
        if isinstance(f, list):
            for x in f:
                if isinstance(x, int):
                    ids.append(x)
                elif isinstance(x, dict) and isinstance(x.get("file"), list):
                    ids.extend(i for i in x["file"] if isinstance(i, int))
        return ids

    @staticmethod
    def _external(acf: dict) -> str:
        if acf.get("external_link"):
            return acf["external_link"]
        f = acf.get("file")
        if isinstance(f, list):
            for x in f:
                if isinstance(x, dict) and x.get("external_link"):
                    return x["external_link"]
        return ""

    def _media(self, ids: set[int]) -> dict[int, str]:
        out: dict[int, str] = {}
        ids_l = sorted(ids)
        for i in range(0, len(ids_l), 100):
            chunk = ids_l[i:i + 100]
            try:
                r = http.get(_MOLE_API + "media", params={"include": ",".join(map(str, chunk)),
                                                          "per_page": 100, "_fields": "id,source_url"})
                for m in r.json() if r.ok else []:
                    out[m["id"]] = m.get("source_url", "")
            except Exception as e:  # noqa: BLE001
                log.debug("MoLE media lookup failed: %s", e)
        return out

    def _build(self, rows: list[tuple[dict, str]], since: date, skip_noise: bool) -> list:
        pending, seen = [], set()
        for row, dtype in rows:
            acf = row.get("acf") or {}
            if not isinstance(acf, dict):
                acf = {}
            title = _clean(acf.get("title") or (row.get("title") or {}).get("rendered", ""))
            if not title:
                continue
            d = _ymd(acf.get("file_date")) or _ymd(acf.get("date")) or _ymd((row.get("date") or "")[:10])
            if d and d < since:
                continue
            if skip_noise and _MOLE_NOISE.search(title):
                continue
            key = re.sub(r"\W+", "", title.lower())[:120]  # same doc is often in several collections
            if key in seen:
                continue
            seen.add(key)
            pending.append((title, d, dtype, self._media_ids(acf), self._external(acf)))
        media = self._media({i for p in pending for i in p[3]})
        items = []
        for title, d, dtype, ids, ext in pending:
            pdf = next((media[i] for i in ids if media.get(i)), "")
            if not pdf and ext.lower().split("?")[0].endswith(".pdf"):
                pdf = ext
            url = pdf or (ext if ext.startswith("http") and "egazette.gov.in/(S(" not in ext else "") or self.homepage
            items.append(self.item(title=title, date=d, url=url, doc_type=dtype, pdf_url=pdf,
                                   ref_no=_ref_from_title(title)))
        items.sort(key=lambda i: i.date or date.min, reverse=True)
        return items


class MoLENotifications(_MoLEBase):
    id = "mole_notifications"
    name = "Ministry of Labour & Employment - Notifications, Labour Code rules & What's New"
    homepage = "https://labour.gov.in/whats-new"

    def fetch(self, since: date):
        rows: list[tuple[dict, str]] = []
        errors = []
        for ptype, params, dtype in (
            ("documents", {"documents_category": 146}, "Gazette Notification"),
            ("central_documents", None, "Notification"),
            ("whats_new", None, "What's New"),
        ):
            try:
                rows += [(r, dtype) for r in self._collect(ptype, since, params)]
            except Exception as e:  # noqa: BLE001
                errors.append(e)
                log.warning("MoLE %s failed: %s", ptype, e)
        if len(errors) == 3:
            raise errors[0]
        items = self._build(rows, since, skip_noise=True)
        for it in items:
            t = it.title.lower()
            if it.doc_type == "Notification":
                if "faq" in t:
                    it.doc_type = "FAQ"
                elif "guideline" in t or "sop" in t.split():
                    it.doc_type = "Guidelines"
                elif re.search(r"\border\b", t):
                    it.doc_type = "Order"
        return items


class MoLEPressReleases(_MoLEBase):
    id = "mole_press_releases"
    name = "Ministry of Labour & Employment - Press Releases"
    homepage = "https://labour.gov.in/press-release"

    def fetch(self, since: date):
        rows = [(r, "Press Release") for r in self._collect("documents", since, {"documents_category": 144})]
        return self._build(rows, since, skip_noise=False)


# ----------------------------------------------------------------------------------------- EPFO

class EPFOCirculars(Source):
    id = "epfo_circulars"
    regulator = "EPFO"
    name = "EPFO - Circulars & Notices"
    group = "Labour"
    homepage = "https://www.epfo.gov.in/circulars/"
    notices_feed = "https://www.epfo.gov.in/feed/?post_type=notices"

    def _notices(self, since: date) -> list:
        items = []
        for page in range(1, 6):
            url = self.notices_feed + (f"&paged={page}" if page > 1 else "")
            r = http.get(url)
            if r.status_code == 404 and page > 1:
                break
            r.raise_for_status()
            x = BeautifulSoup(r.content, "xml")
            entries = x.find_all("item")
            if not entries:
                break
            oldest = None
            for it in entries:
                title = _clean(it.title.text if it.title else "")
                link = _clean(it.link.text if it.link else "")
                pub = parse_date(it.pubDate.text if it.pubDate else "")
                d = find_date(title) or pub  # "Circular Dated 28.09.2026" -> issue date
                oldest = pub if oldest is None or (pub and pub < oldest) else oldest
                if d and d < since:
                    continue
                body = (it.find("encoded").text if it.find("encoded") else "") + (it.description.text if it.description else "")
                pdf = ""
                for a in BeautifulSoup(body, "lxml").find_all("a", href=True):
                    if re.search(r"\.pdf($|\?)", a["href"], re.I):
                        pdf = http.absolute(link, a["href"])
                        break
                t = title.lower()
                dtype = "Circular" if "circular" in t else ("FAQ" if "faq" in t else "Notice")
                items.append(self.item(title=title, date=d, url=link or pdf, doc_type=dtype, pdf_url=pdf,
                                       ref_no=_ref_from_title(title)))
            if oldest and oldest < since:
                break
        return items

    def _circulars(self, since: date) -> list:
        s, r = http.soup(self.homepage, timeout=90)
        items = []
        for box in s.select("div.download-box"):
            h = box.select_one(".download-title") or box.find(["h3", "h4"])
            title = _clean(h.get_text(" ")) if h else ""
            if not title:
                continue
            d = None
            metas = [_clean(m.get_text(" ")) for m in box.select(".meta-item")]
            for m in reversed(metas):
                d = parse_date(m)  # dd/mm/yyyy
                if d:
                    break
            if d is None:
                d = find_date(title)
            # Undated cards are legacy entries (~200 of ~1900); including them would flood every run.
            if d is None or d < since:
                continue
            a = box.find("a", href=True)
            pdf = http.absolute(self.homepage, a["href"]) if a else ""
            items.append(self.item(title=title, date=d, url=pdf or self.homepage, doc_type="Circular",
                                   pdf_url=pdf if pdf.lower().split("?")[0].endswith(".pdf") else "",
                                   ref_no=_ref_from_title(title)))
        return items

    def fetch(self, since: date):
        items, errors = [], []
        for fn in (self._notices, self._circulars):
            try:
                items += fn(since)
            except Exception as e:  # noqa: BLE001
                errors.append(e)
                log.warning("EPFO %s failed: %s", fn.__name__, e)
        if len(errors) == 2:
            raise errors[0]
        seen, out = set(), []
        for it in items:
            k = it.pdf_url or it.url
            if k in seen:
                continue
            seen.add(k)
            out.append(it)
        out.sort(key=lambda i: i.date or date.min, reverse=True)
        return out


# ----------------------------------------------------------------------------------------- ESIC

class ESICCirculars(Source):
    id = "esic_circulars"
    regulator = "ESIC"
    name = "ESIC - Instructions / Circulars / Orders"
    group = "Labour"
    homepage = "https://www.esic.gov.in/circulars"
    max_pages = 40

    # Rows from hospitals / regional offices are local admin (seniority lists, empanelments, drug purchase).
    _FIELD = re.compile(r"hospital|regional office|sub[- ]regional|medical college|dispensar|branch office|"
                        r"\bSRO\b|\bRO\b|\bDCBO\b|ESIC?\s+H|PGIMSR|zonal", re.I)
    _SKIP_LINK = re.compile(r"^\s*(annex|encl|appendix|attachment|forwarding|list of|hindi)", re.I)

    @staticmethod
    def _link_title(a) -> str:
        t = _clean(a.get_text(" "))
        t = re.sub(r"\s*-?\s*PDF\s*(size\s*:?\s*\([^)]*\))?\s*\.?\s*$", "", t, flags=re.I)
        return t.strip(" -.")

    def fetch(self, since: date):
        items, seen = [], set()
        for page in range(1, self.max_pages + 1):
            url = self.homepage if page == 1 else f"{self.homepage}/index/page:{page}"
            s, r = http.soup(url)
            rows = [tr for tr in s.select("table tr") if len(tr.find_all("td")) >= 5]
            if not rows:
                break
            oldest = None
            for tr in rows:
                td = tr.find_all("td")
                branch = _clean(td[1].get_text(" "))
                no_dated = _clean(td[2].get_text(" "))
                links = [a for a in td[3].find_all("a", href=True)]
                published = parse_date(_clean(td[4].get_text(" ")))
                console = _clean(td[5].get_text(" ")) if len(td) > 5 else ""
                if published and (oldest is None or published < oldest):
                    oldest = published
                issued = find_date(no_dated)  # dd-mm-yyyy
                d = issued or published
                if d and d < since:
                    continue
                titles = [(self._link_title(a), a) for a in links]
                main = titles[0] if titles else (_clean(td[3].get_text(" ")), None)
                if main[0] and _mostly_devanagari(main[0]):  # Hindi first: prefer an English link
                    for t, a in titles[1:]:
                        if t and not _mostly_devanagari(t) and not self._SKIP_LINK.match(t):
                            main = (t, a)
                            break
                title, a = main
                if not title:
                    continue
                pdf = http.absolute(url, a["href"]) if a is not None else ""
                ref = re.sub(r"\s*(dated|dt\.?)?\s*\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}\s*$", "", no_dated, flags=re.I).strip(" ,-")
                if not re.search(r"\d", ref) and console:  # "Office Order" alone is not a reference
                    ref = f"Console No. {console}"
                k = pdf or (title, d)
                if k in seen:
                    continue
                seen.add(k)
                items.append(self.item(title=title, date=d, url=pdf or self.homepage,
                                       doc_type="Circular - Field Office" if self._FIELD.search(branch) else "Circular",
                                       ref_no=ref, pdf_url=pdf))
            if oldest and oldest < since:
                break
        return items


SOURCES = (MoLENotifications, MoLEPressReleases, EPFOCirculars, ESICCirculars)
