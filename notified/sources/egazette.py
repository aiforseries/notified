"""e-Gazette of India (egazette.gov.in) - Central Government gazette notifications, one source per ministry.

Route: the public "Search by Ministry" form (SearchMinistry.aspx, ASP.NET WebForms, no CAPTCHA/login),
used in "Date Wise" mode with a from/to date range. The site uses cookieless sessions
(/(S(xxxx))/ in the URL) and only serves inner pages when navigated from its own pages (Referer),
so we walk it like a browser: home -> SearchMenu -> "Search by Ministry" -> pick ministry ->
"Date Wise" -> submit -> GridView pages (15 rows each, newest issue date first).

The listing has no G.S.R./S.O. number, so ref_no is read from the start of the gazette PDF
(HTTP Range request for the first few hundred KB, which the server supports); if that fails
the Gazette ID (e.g. "CG-DL-E-04112026-277682") is used instead.
"""
from __future__ import annotations

import re
import zlib
from datetime import date, timedelta

from bs4 import BeautifulSoup

from .. import http
from ..dates import parse_date
from .base import Source

HOME = "https://egazette.gov.in/"
SEARCH_PAGE = "https://egazette.gov.in/SearchMenu.aspx"
TIMEOUT = 120
MAX_PAGES = 15                  # 15 rows/page; Finance (busiest) is ~7 pages for 60 days
SLACK_DAYS = 10                 # search on issue date; publish date can lag issue date by a few days
REF_HEAD_BYTES = 393216         # bytes of each PDF read to find the G.S.R./S.O. number
MAX_REF_LOOKUPS = 60            # cap PDF reads per fetch (only matters for long back-fills)

# The notification's own number sits right after its date line ("New Delhi, the 3rd November, 2026
# G.S.R. 968(E).--"). Numbers elsewhere in the text are usually earlier notifications being amended,
# so only the one anchored to the date line is accepted.
_REF_RX = re.compile(
    r"New\s*Delhi\s*,?\s*the\s+[^.]{0,40}?\b(?:1|2)\s*(?:9|0)\s*\d\s*\d\s*[.,]?\s*"
    r"(?:[^()]{0,45}?\s)?"                         # optional heading, e.g. "INCOME - TAX", "No. 132 of 2026 - CBDT"
    r"(G\s*\.\s*S\s*\.\s*R|S\s*\.\s*O)\s*\.?\s*(?:No\.?\s*)?((?:\d\s*){1,6})\(\s*E\s*\)", re.I)
_ID_RX = re.compile(r"-(\d{6,})$")


def _form(b: BeautifulSoup) -> dict:
    d = {i["name"]: i.get("value", "") for i in b.find_all("input", attrs={"type": ["hidden", "text"]}) if i.get("name")}
    for s in b.find_all("select"):
        o = s.find("option", selected=True) or s.find("option")
        if o is not None and s.get("name"):
            d[s["name"]] = o.get("value", "")
    for i in b.find_all("input", attrs={"type": "radio", "checked": True}):
        d[i["name"]] = i.get("value", "")
    d["__EVENTTARGET"] = d["__EVENTARGUMENT"] = ""
    return d


def _pdf_url(gazette_id: str, d: date | None) -> str:
    m = _ID_RX.search(gazette_id or "")
    if not m:
        return ""
    year = d.year if d else None
    dm = re.search(r"-(\d{2})(\d{2})(\d{4})-", gazette_id)   # CG-DL-E-ddmmyyyy-NNNNNN
    if dm:
        year = int(dm.group(3))
    return f"{HOME}WriteReadData/{year}/{m.group(1)}.pdf" if year else ""


def _pdf_text(raw: bytes) -> str:
    """Very small text pull from (possibly truncated) PDF bytes: inflate complete streams and
    join the literal strings inside BT..ET blocks. Good enough to find "G.S.R. 968(E)"."""
    chunks = []
    for m in re.finditer(rb"stream\r?\n(.*?)endstream", raw, re.S):
        try:
            s = zlib.decompress(m.group(1))
        except Exception:  # noqa: BLE001 - truncated/non-flate stream
            continue
        for bt in re.findall(rb"BT(.*?)ET", s, re.S):
            parts = re.findall(rb"\(((?:\\.|[^\\)])*)\)", bt)
            if parts:
                chunks.append(b"".join(parts))
    return b" ".join(chunks).decode("latin-1").replace("\\", "")


def _ref_from_pdf(url: str) -> str:
    r = http.get(url, timeout=60, retries=1, headers={"Range": f"bytes=0-{REF_HEAD_BYTES - 1}", "Referer": HOME})
    if r.status_code not in (200, 206):
        return ""
    m = _REF_RX.search(_pdf_text(r.content[:REF_HEAD_BYTES]))
    if not m:
        return ""
    kind = "G.S.R." if "R" in m.group(1).upper() else "S.O."
    num = re.sub(r"\s+", "", m.group(2))
    return f"{kind} {num}(E)"


class _EgazetteMinistry(Source):
    group = ""
    doc_type = "Gazette Notification"
    ministry_value = ""          # ddlMinistry option value
    ministry_name = ""           # rows from any other ministry/organisation are dropped
    homepage = SEARCH_PAGE

    def keep(self, ministry: str, dept: str, office: str, subject: str) -> bool:
        return True

    # --- site walk -------------------------------------------------------------------------
    def _post(self, url: str, data: dict):
        r = http.post(url, data=data, timeout=TIMEOUT, headers={"Referer": url})
        r.raise_for_status()
        return BeautifulSoup(http.text(r), "lxml")

    def _search(self, since: date):
        """Yields BeautifulSoup result pages (newest issue date first)."""
        r = http.get(HOME, timeout=TIMEOUT)
        r.raise_for_status()
        base = r.url                                  # carries the /(S(session))/ segment
        r = http.get(http.absolute(base, "SearchMenu.aspx"), timeout=TIMEOUT, headers={"Referer": base})
        r.raise_for_status()
        url = r.url
        if "error.aspx" in url.lower():
            raise RuntimeError("e-Gazette refused the search page (error.aspx)")
        b = BeautifulSoup(http.text(r), "lxml")
        d = _form(b)
        d["btnMinistry"] = "Search by Ministry"
        r = http.post(url, data=d, timeout=TIMEOUT, headers={"Referer": url})
        r.raise_for_status()
        url = r.url
        b = BeautifulSoup(http.text(r), "lxml")
        if not b.find(id="ddlMinistry"):
            raise RuntimeError("e-Gazette: 'Search by Ministry' form not found")
        d = _form(b)
        d.update(ddlMinistry=self.ministry_value, __EVENTTARGET="ddlMinistry")
        b = self._post(url, d)
        d = _form(b)
        d.update(rdb_Option="1", __EVENTTARGET="rdb_Option$1")
        b = self._post(url, d)
        d = _form(b)
        d.update(ddlMinistry=self.ministry_value, rdb_Option="1",
                 txtDateFrom=(since - timedelta(days=SLACK_DAYS)).strftime("%d-%b-%Y"),
                 txtDateTo=date.today().strftime("%d-%b-%Y"))
        d["ImgSubmitDetails.x"], d["ImgSubmitDetails.y"] = "10", "10"
        b = self._post(url, d)
        yield b
        for page in range(2, MAX_PAGES + 1):
            if not b.find("a", href=re.compile(rf"Page\${page}'")):
                return
            d = _form(b)
            d.update(__EVENTTARGET="gvGazetteList", __EVENTARGUMENT=f"Page${page}")
            b = self._post(url, d)
            yield b

    # --- parsing ---------------------------------------------------------------------------
    @staticmethod
    def _rows(b: BeautifulSoup):
        g = b.find(id="gvGazetteList")
        if not g:
            return
        for i in range(200):
            def f(k):
                e = g.find(id=f"gvGazetteList_lbl_{k}_{i}")
                return e.get_text(" ", strip=True) if e else None
            subject = f("Subject")
            if subject is None:
                break
            yield dict(ministry=f("Ministry") or "", dept=f("Department") or "", office=f("Office") or "",
                       subject=" ".join(subject.split()), meta=" ".join((f("metadata") or "").split()), category=f("Category") or "",
                       part=f("PartSection") or "", issued=parse_date(f("IssueDate")),
                       published=parse_date(f("PublishDate")), gid=f("UGID") or "")

    def fetch(self, since: date):
        items, seen, lookups = [], set(), 0
        for b in self._search(since):
            rows = list(self._rows(b))
            for row in rows:
                if row["gid"] in seen:
                    continue
                seen.add(row["gid"])
                d = row["published"] or row["issued"]
                if d and d < since:
                    continue
                if " ".join(row["ministry"].lower().split()) != self.ministry_name.lower():
                    continue
                if not self.keep(row["ministry"], row["dept"], row["office"], row["subject"] + " " + row["meta"]):
                    continue
                pdf = _pdf_url(row["gid"], d)
                ref = ""
                if pdf and lookups < MAX_REF_LOOKUPS:
                    lookups += 1
                    try:
                        ref = _ref_from_pdf(pdf)
                    except Exception:  # noqa: BLE001 - ref number is a nice-to-have
                        ref = ""
                items.append(self.item(title=row["subject"], date=d, url=pdf or self.homepage,
                                       doc_type=self.doc_type, ref_no=ref or row["gid"], pdf_url=pdf))
            # pages are ordered by issue date (newest first): stop once a whole page is older
            issued = [r["issued"] for r in rows if r["issued"]]
            if not rows or (issued and max(issued) < since - timedelta(days=SLACK_DAYS)):
                break
        return items


class EgazetteMCA(_EgazetteMinistry):
    id, regulator, group = "egazette_mca", "MCA", "MCA"
    name = "e-Gazette - Ministry of Corporate Affairs"
    ministry_value, ministry_name = "19", "Ministry of Corporate Affairs"


class EgazetteMeitY(_EgazetteMinistry):
    id, regulator, group = "egazette_meity", "MeitY", "Data Protection"
    name = "e-Gazette - Ministry of Electronics and IT (DPDP / IT Act)"
    ministry_value, ministry_name = "397", "Ministry of Electronics and Information Technology"


class EgazetteCBDT(_EgazetteMinistry):
    id, regulator, group = "egazette_cbdt", "CBDT", "Direct Tax"
    name = "e-Gazette - CBDT (Income-tax notifications)"
    ministry_value, ministry_name = "18", "Ministry of Finance"   # Ministry of Finance; filtered to Dept of Revenue / CBDT below

    _IT_RX = re.compile(r"income[\s-]*tax|direct tax|black money|\bTDS\b|\bTCS\b", re.I)

    def keep(self, ministry, dept, office, subject):
        o = office.lower()
        if "indirect" in o or "customs" in o or "cbic" in o:
            return False
        if "board of direct taxes" in o or o == "cbdt":
            return True
        # a few Revenue notifications carry no office; keep those only when clearly income-tax
        return "revenue" in dept.lower() and office.lower() in ("", "not applicable") and bool(self._IT_RX.search(subject))


class EgazetteLabour(_EgazetteMinistry):
    id, regulator, group = "egazette_labour", "MoLE", "Labour"
    name = "e-Gazette - Ministry of Labour and Employment"
    ministry_value, ministry_name = "28", "Ministry of Labour and Employment"


SOURCES = (EgazetteMCA, EgazetteMeitY, EgazetteCBDT, EgazetteLabour)
