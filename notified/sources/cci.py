"""Competition Commission of India - regulations & amendments, press releases
(policy releases vs. routine combination approvals) and "What's New".

cci.gov.in listing pages are Laravel/yajra DataTables; we call the same JSON endpoints
with the standard DataTables query string (columns[] are required, else HTTP 500).
"""
from __future__ import annotations

import html
import json
import re
from datetime import date

from bs4 import BeautifulSoup

from .. import http
from ..dates import find_date, parse_date
from .base import Source

CCI = "https://www.cci.gov.in"
PAGE_SIZE = 25
MAX_PAGES = 8
_H = {"X-Requested-With": "XMLHttpRequest", "Accept": "application/json, text/javascript, */*; q=0.01"}
_APPROVAL_RX = re.compile(r"^\s*(the\s+)?(commission|cci)\s+(approves|clears)\b", re.I)
_RECRUIT_RX = re.compile(r"\b(filling\s+up\s+of\s+(the\s+)?posts?|vacanc(y|ies)|deputation|recruitment|"
                         r"empanel\w*|tender|expression\s+of\s+interest|eoi|internship|inviting\s+applications)\b",
                         re.I)


def _text(s) -> str:
    s = str(s or "")
    if "<" in s:
        s = BeautifulSoup(s, "lxml").get_text(" ")
    return " ".join(html.unescape(s).replace("\xa0", " ").split())


def _href(s) -> str:
    m = re.search(r"""href=["']([^"']+)["']""", str(s or ""))
    return http.absolute(CCI + "/", html.unescape(m.group(1))) if m else ""


def _first_file(file_content) -> str:
    try:
        files = json.loads(html.unescape(str(file_content or "")))
    except (ValueError, TypeError):
        return ""
    for f in files if isinstance(files, list) else []:
        fn = (f or {}).get("file_name") or ""
        if fn:
            return http.absolute(CCI + "/", fn.replace("\\/", "/"))
    return ""


def _dt_params(cols, start, since):
    p = {"draw": 1 + start // PAGE_SIZE, "start": start, "length": PAGE_SIZE,
         "order[0][column]": 0, "order[0][dir]": "desc", "search[value]": "", "search[regex]": "false",
         "searchString": "", "fromdate": since.isoformat(), "todate": date.today().isoformat()}
    for i, c in enumerate(cols):
        p.update({f"columns[{i}][data]": c, f"columns[{i}][name]": c, f"columns[{i}][searchable]": "true",
                  f"columns[{i}][orderable]": "true", f"columns[{i}][search][value]": "",
                  f"columns[{i}][search][regex]": "false"})
    return p


class _CciTable(Source):
    regulator = "CCI"
    group = "Corporate Law (ICSI/CCI)"
    endpoints: tuple = ()      # ((ajax_url, page_url), ...)
    cols = ("DT_RowIndex", "title", "order_date", "files")
    date_field = "order_date"
    doc_type = ""

    @property
    def homepage(self) -> str:  # type: ignore[override]
        return self.endpoints[0][1]

    def keep(self, title: str) -> bool:
        return True

    def _rows(self, ajax, page_url, since):
        for page in range(MAX_PAGES):
            r = http.get(ajax, params=_dt_params(self.cols, page * PAGE_SIZE, since),
                         headers={**_H, "Referer": page_url})
            r.raise_for_status()
            j = json.loads(http.text(r))
            rows = j.get("data") or []
            yield from rows
            total = int(j.get("recordsFiltered") or 0)
            if not rows or (page + 1) * PAGE_SIZE >= total:
                return
            ds = [parse_date(_text(x.get(self.date_field))) for x in rows]
            ds = [d for d in ds if d]
            if ds and min(ds) < since:
                return

    def fetch(self, since: date):
        items, seen = [], set()
        for ajax, page_url in self.endpoints:
            for x in self._rows(ajax, page_url, since):
                title = _text(x.get("title"))
                if not title or not self.keep(title):
                    continue
                d = parse_date(_text(x.get(self.date_field))) or find_date(title)
                if d and d < since:
                    continue
                pdf = _first_file(x.get("file_content"))
                link = _href(x.get("files")) or _href(x.get("file_name")) or _href(x.get("title")) or pdf or page_url
                if not pdf and link.lower().endswith(".pdf"):
                    pdf = link
                key = (title.lower(), pdf or link)
                if key in seen:
                    continue
                seen.add(key)
                items.append(self.item(title=title, date=d, url=link, doc_type=self.doc_type, pdf_url=pdf))
        return items


class CciRegulations(_CciTable):
    id, name, doc_type = "cci_regulations", "CCI - Regulations & Amendments", "Regulation"
    endpoints = (
        (f"{CCI}/legal-framwork/fetch-regulationslist", f"{CCI}/legal-framwork/regulations"),
        (f"{CCI}/legal-framwork/fetch-regulationsamendmentlist", f"{CCI}/legal-framwork/regulations/amendment"),
        (f"{CCI}/combination/legal-framwork/regulations", f"{CCI}/combination/legal-framwork/regulations"),
    )


class CciPressReleases(_CciTable):
    """Policy / enforcement press releases - routine merger approvals are split out below."""
    id, name, doc_type = "cci_press_releases", "CCI - Press Releases", "Press Release"
    endpoints = ((f"{CCI}/media-gallery/press-release", f"{CCI}/media-gallery/press-release"),)

    def keep(self, title):
        return not _APPROVAL_RX.search(title)


class CciCombinationApprovals(_CciTable):
    """'Commission approves acquisition of ...' releases: several a week, deal-specific."""
    id, name, doc_type = "cci_combination_approvals", "CCI - Combination Approvals (press releases)", "Press Release"
    endpoints = ((f"{CCI}/media-gallery/press-release", f"{CCI}/media-gallery/press-release"),)
    enabled_by_default = False

    def keep(self, title):
        return bool(_APPROVAL_RX.search(title))


class CciWhatsNew(_CciTable):
    """Public notices, consultations, gazette notifications; recruitment/tender items dropped."""
    id, name, doc_type = "cci_whats_new", "CCI - What's New / Public Notices", "Notice"
    endpoints = ((f"{CCI}/whats-new", f"{CCI}/whats-new"),)
    cols = ("DT_RowIndex", "title", "file_name")
    date_field = "new_date"

    def keep(self, title):
        return not _RECRUIT_RX.search(title)


SOURCES = (CciRegulations, CciPressReleases, CciCombinationApprovals, CciWhatsNew)
