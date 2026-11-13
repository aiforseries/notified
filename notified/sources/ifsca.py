"""IFSCA (GIFT City) - circulars, regulations, notifications, guidelines and press releases.

The public listing pages are DataTables filled from a JSON endpoint
(/Legal/GetLegalData, /PressRelease/GetLegalData) keyed by the page's EncryptedId.
Rows come newest first; we page until we pass `since`.
"""
from __future__ import annotations

import json
from datetime import date
from urllib.parse import quote

from .. import http
from ..dates import parse_date
from .base import Source

IFSCA = "https://ifsca.gov.in"
PAGE_SIZE = 25
MAX_PAGES = 8


def _clean(s) -> str:
    return " ".join(str(s or "").replace("\xa0", " ").split())


class _IfscaListing(Source):
    regulator = "IFSCA"
    group = "IFSC / GIFT City"
    section = "Legal"          # "Legal" or "PressRelease"
    eid = ""                   # EncryptedId from the menu link
    doc_type = ""

    @property
    def homepage(self) -> str:  # type: ignore[override]
        return f"{IFSCA}/{self.section}/Index/{self.eid}"

    def _page(self, page: int) -> list[dict]:
        params = dict(draw=page, start=(page - 1) * PAGE_SIZE, length=PAGE_SIZE, PageNumber=page,
                      PageSize=PAGE_SIZE, SearchText="", EncryptedId=self.eid, DateFrom="", DateTo="",
                      AIlistType="")
        r = http.get(f"{IFSCA}/{self.section}/GetLegalData", params=params,
                     headers={"X-Requested-With": "XMLHttpRequest", "Referer": self.homepage,
                              "Accept": "application/json"})
        r.raise_for_status()
        data = json.loads(http.text(r)).get("data") or {}
        return data.get("LegalMasterModelList") or []

    def fetch(self, since: date):
        items, seen = [], set()
        for page in range(1, MAX_PAGES + 1):
            rows = self._page(page)
            if not rows:
                if page == 1:
                    raise RuntimeError("IFSCA listing returned no rows (EncryptedId may have changed)")
                break
            oldest, new = None, 0
            for x in rows:
                title = _clean(x.get("Title"))
                if not title:
                    continue
                d = parse_date(_clean(x.get("PublishDate")))
                fid, fname = _clean(x.get("PhotoFileID")), _clean(x.get("PhotoFileName"))
                key = (x.get("LegalId"), fid, title)
                if key in seen:
                    continue
                seen.add(key)
                new += 1
                if d:
                    oldest = d if oldest is None else min(oldest, d)
                if d and d < since:
                    continue
                pdf = view = ""
                if fid and fname:
                    pdf = f"{IFSCA}/CommonDirect/DownloadFile?id={quote(fid)}&fileName={quote(fname)}"
                    view = (f"{IFSCA}/CommonDirect/GetFileView?id={quote(fid)}&fileName={quote(fname)}"
                            f"&TitleName={quote('Press Release' if self.section == 'PressRelease' else 'Legal')}")
                items.append(self.item(title=title, date=d, url=view or self.homepage, doc_type=self.doc_type,
                                       pdf_url=pdf if fname.lower().endswith(".pdf") else ""))
            if not new or (oldest and oldest < since):
                break
            total_pages = ((rows[0].get("PaginationRequest") or {}).get("TotalPage") or 0)
            if total_pages and page >= total_pages:
                break
        return items


class IfscaCirculars(_IfscaListing):
    id, name, eid, doc_type = "ifsca_circulars", "IFSCA - Circulars", "wF6kttc1JR8=", "Circular"


class IfscaRegulations(_IfscaListing):
    id, name, eid, doc_type = "ifsca_regulations", "IFSCA - Regulations / Amendments", "ogGPf3wx5GE=", "Regulation"


class IfscaNotifications(_IfscaListing):
    id, name, eid, doc_type = "ifsca_notifications", "IFSCA - Notifications", "zcGvy-Iqfcg=", "Notification"


class IfscaGuidelines(_IfscaListing):
    id, name, eid, doc_type = "ifsca_guidelines", "IFSCA - Guidelines", "mizvnmwVAgs=", "Guideline"


class IfscaPressReleases(_IfscaListing):
    id, name, section, eid, doc_type = ("ifsca_press_releases", "IFSCA - Press Releases", "PressRelease",
                                        "MEdJSLhva0M=", "Press Release")


SOURCES = (IfscaCirculars, IfscaRegulations, IfscaNotifications, IfscaGuidelines, IfscaPressReleases)
