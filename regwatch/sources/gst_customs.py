"""GST & Customs: CBIC tax-information portal (notifications, circulars, instructions) and GSTN news/advisories.

CBIC: https://taxinformation.cbic.gov.in/ is an Angular (JHipster) SPA backed by an open JSON API under /api/.
  The API needs a `language: en` request header (the SPA's interceptor adds it); without it the
  list endpoints answer HTTP 500. Lists come newest-first and page with page/size (offset = page*size),
  scoped to one calendar year, so we walk year by year from today back to `since.year`.
  The API's own download endpoint returns the PDF base64-wrapped in JSON, so it is not a usable link;
  `url` is the portal's viewer page (/view-pdf/{id}/ENG/{tab}) and pdf_url is left empty.

GSTN: https://www.gst.gov.in/newsandupdates is an AngularJS page that loads /fomessage/newsupdates
  (JSON, ~20 latest items, dates dd/mm/yyyy). The gst.gov.in WAF rejects the curl_cffi browser
  impersonation used by regwatch.http ("Request Rejected") but serves ordinary requests, so this
  feed uses plain `requests`.
"""
from __future__ import annotations

import re
from datetime import date

import requests

from .. import http
from ..dates import parse_date
from .base import Source

CBIC = "https://taxinformation.cbic.gov.in"
CBIC_API = CBIC + "/api"
CBIC_HEADERS = {"language": "en", "Accept": "application/json"}

TAX_GST = 1000001
TAX_CUSTOMS = 1000002

PAGE_SIZE = 50
MAX_PAGES = 20  # per year; safety cap


def _iso_date(v) -> date | None:
    """CBIC dates look like '2026-10-28T05:30:00+05:30' (IST midnight). Take the calendar date as shown."""
    if not v:
        return None
    if isinstance(v, (int, float)):  # epoch ms, defensive
        from datetime import datetime, timedelta, timezone
        return (datetime.fromtimestamp(v / 1000, tz=timezone.utc) + timedelta(hours=5, minutes=30)).date()
    s = str(v)
    m = re.match(r"(\d{4}-\d{2}-\d{2})", s)
    return parse_date(m.group(1)) if m else parse_date(s)


def _clean(s) -> str:
    return " ".join(str(s or "").split()).strip()


def _fix_text(s: str) -> str:
    # CBIC and GSTN feeds ship some dashes/quotes as U+FFFD already.
    s = _clean(s).replace(" � ", " - ").replace("�", "'")
    return s


class _CbicFeed(Source):
    """One CBIC document type for one tax (GST or Customs)."""
    group = "GST & Customs"
    regulator = "CBIC"
    tax_id: int = TAX_GST
    kind: str = "notification"   # notification | circular | instruction
    doc_type: str = "Notification"
    tab: str = "Notifications"   # path segment used by the portal's PDF viewer
    ref_prefix: str = ""         # e.g. "Notification No. "
    categories: tuple[str, ...] = ()  # if set, keep only these categories (client-side filter)

    _ENDPOINTS = {
        "notification": "cbic-notification-msts/fetchNotificationByYearAndCategory",
        "circular": "cbic-circular-msts/fetchCircularByYearCategory",
        "instruction": "cbic-instruction-msts/fetch-instructions-year-category",
    }

    def _page(self, year: int, page: int) -> list[dict]:
        r = http.get(f"{CBIC_API}/{self._ENDPOINTS[self.kind]}",
                     params={"year": year, "page": page, "size": PAGE_SIZE, "taxId": self.tax_id},
                     headers=CBIC_HEADERS)
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, list):
            raise RuntimeError(f"unexpected CBIC response: {str(data)[:200]}")
        return data

    def _to_item(self, rec: dict):
        k = self.kind
        no = re.sub(r"(\d{4})\s*-\s*", r"\1-", _clean(rec.get(f"{k}No")))
        name = _fix_text(rec.get(f"{k}Name"))
        cat = _clean(rec.get(f"{k}Category"))
        d = _iso_date(rec.get(f"{k}Dt")) or _iso_date(rec.get("issueDt")) or _iso_date(rec.get("createdDt"))
        ref = ""
        if no:
            word = self.ref_prefix.split()[0].lower() if self.ref_prefix else ""
            ref = (no if not word or no.lower().startswith(word) else self.ref_prefix + no).rstrip(".")
        rid = rec.get("id")
        url = f"{CBIC}/view-pdf/{rid}/ENG/{self.tab}" if rid else self.homepage
        title = name or ref or "(untitled)"
        return self.item(title=title, date=d, url=url, doc_type=self.doc_type, ref_no=ref, pdf_url=""), cat

    def fetch(self, since: date) -> list:
        out, seen = [], set()
        for year in range(date.today().year, since.year - 1, -1):
            for page in range(MAX_PAGES):
                recs = self._page(year, page)
                if not recs:
                    break
                past = False
                for rec in recs:
                    it, cat = self._to_item(rec)
                    if it.date and it.date < since:
                        past = True
                        continue
                    if self.categories and cat not in self.categories:
                        continue
                    if it.uid in seen:
                        continue
                    seen.add(it.uid)
                    out.append(it)
                if past or len(recs) < PAGE_SIZE:
                    break
        return out


class CbicGstNotifications(_CbicFeed):
    id = "cbic_gst_notifications"
    name = "CBIC - GST notifications (Central/Integrated/UT Tax, Rate, Compensation Cess)"
    homepage = CBIC + "/content-page/explore-notification"
    tax_id, kind, doc_type, tab, ref_prefix = TAX_GST, "notification", "Notification", "Notifications", "Notification No. "


class CbicGstCirculars(_CbicFeed):
    id = "cbic_gst_circulars"
    name = "CBIC - GST circulars (CGST, IGST, Compensation Cess)"
    homepage = CBIC + "/content-page/explore-circulars"
    tax_id, kind, doc_type, tab, ref_prefix = TAX_GST, "circular", "Circular", "Circulars", "Circular No. "


class CbicGstInstructions(_CbicFeed):
    id = "cbic_gst_instructions"
    name = "CBIC - GST instructions"
    homepage = CBIC + "/"
    tax_id, kind, doc_type, tab, ref_prefix = TAX_GST, "instruction", "Instruction", "Instructions", "Instruction No. "


class CbicCustomsNotifications(_CbicFeed):
    id = "cbic_customs_notifications"
    name = "CBIC - Customs notifications (Tariff, Non-Tariff, ADD, CVD, Safeguard)"
    homepage = CBIC + "/content-page/explore-notification"
    tax_id, kind, doc_type, tab, ref_prefix = TAX_CUSTOMS, "notification", "Notification", "Notifications", "Notification No. "


class CbicCustomsCirculars(_CbicFeed):
    id = "cbic_customs_circulars"
    name = "CBIC - Customs circulars"
    homepage = CBIC + "/content-page/explore-circulars"
    tax_id, kind, doc_type, tab, ref_prefix = TAX_CUSTOMS, "circular", "Circular", "Circulars", "Circular No. "

    def _to_item(self, rec):
        it, cat = super()._to_item(rec)
        if it.ref_no and "customs" not in it.ref_no.lower():
            it.ref_no += "-Customs"
        return it, cat


class CbicCustomsInstructions(_CbicFeed):
    id = "cbic_customs_instructions"
    name = "CBIC - Customs instructions"
    homepage = CBIC + "/"
    tax_id, kind, doc_type, tab, ref_prefix = TAX_CUSTOMS, "instruction", "Instruction", "Instructions", "Instruction No. "


# ---------------------------------------------------------------- GSTN

GSTN = "https://www.gst.gov.in"
_HREF_RX = re.compile(r'href\s*=\s*["\']([^"\']+)["\']', re.I)
_GSTN_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Notified",
            "Accept": "application/json, text/plain, */*"}


class GstnAdvisories(Source):
    id = "gstn_advisories"
    regulator = "GSTN"
    name = "GSTN - News & advisories (GST portal)"
    group = "GST & Customs"
    homepage = GSTN + "/newsandupdates"

    def fetch(self, since: date) -> list:
        # Plain requests on purpose: the gst.gov.in WAF rejects the curl_cffi impersonated client.
        r = requests.get(GSTN + "/fomessage/newsupdates", headers=_GSTN_UA, timeout=http.DEFAULT_TIMEOUT)
        r.raise_for_status()
        data = r.json()
        rows = data.get("data", []) if isinstance(data, dict) else data
        out = []
        for row in rows or []:
            title = _fix_text(row.get("title"))
            if not title:
                continue
            d = parse_date(_clean(row.get("date")))  # dd/mm/yyyy
            if d and d < since:
                continue
            link = _clean(row.get("linkURl"))
            if link:
                url = http.absolute(GSTN + "/", link if "//" not in link[:2] else "https:" + link)
            else:
                url = f"{GSTN}/newsandupdates/read/{row.get('id')}"
            pdf = ""
            for h in _HREF_RX.findall(row.get("content") or ""):
                if h.lower().split("?")[0].endswith(".pdf"):
                    pdf = http.absolute(GSTN + "/", "https:" + h if h.startswith("//") else h)
                    break
            if not pdf and url.lower().endswith(".pdf"):
                pdf = url
            low = title.lower()
            doc_type = "Advisory" if "advisory" in low else "Press Release" if "press release" in low else "News"
            out.append(self.item(title=title, date=d, url=url, doc_type=doc_type, pdf_url=pdf))
        return out


SOURCES = (
    CbicGstNotifications,
    CbicGstCirculars,
    CbicGstInstructions,
    CbicCustomsNotifications,
    CbicCustomsCirculars,
    CbicCustomsInstructions,
    GstnAdvisories,
)
