"""Press Information Bureau (PIB) - press releases filtered by ministry.

Uses the public "All Releases" page (English, Delhi region). It is an ASP.NET WebForms
page: the ministry / day / month / year dropdowns post back with __VIEWSTATE. Day "All"
(value 0) lists a whole month, so we post once per month from today back to `since`.
Each month block is headed by the ministry name; only rows under the requested
ministry's heading are taken, so other ministries cannot leak in.

pib_cbdt / pib_cbic are title-keyword subsets of the Ministry of Finance listing
(which is mostly DRI/CBN enforcement news); the month pages are shared between
them via a small in-process cache so each month is fetched once per run.
"""
from __future__ import annotations

import re
import threading
from datetime import date

from bs4 import BeautifulSoup

from .. import http
from ..dates import parse_date
from .base import Source

BASE = "https://pib.gov.in/"
LISTING = "https://pib.gov.in/allRel.aspx?reg=3&lang=1"
P = "ctl00$ContentPlaceHolder1$"
MAX_MONTHS = 24

_lock = threading.Lock()
_cache: dict[tuple[str, int, int], list[tuple[date | None, str, str]]] = {}
_month_locks: dict[tuple[str, int, int], threading.Lock] = {}
_form_cache: dict[str, tuple[str, dict]] = {}


def _form(b: BeautifulSoup) -> dict:
    data = {i["name"]: i.get("value", "") for i in b.find_all("input")
            if i.get("name") and (i.get("type") or "").lower() == "hidden"}
    for s in b.find_all("select"):
        if not s.get("name"):
            continue
        o = s.find("option", selected=True) or s.find("option")
        if o is not None:
            data[s["name"]] = o.get("value", "")
    return data


def _month_rows(ministry_id: str, ministry_name: str, y: int, m: int) -> list[tuple[date | None, str, str]]:
    key = (ministry_id, y, m)
    with _lock:
        ml = _month_locks.setdefault(key, threading.Lock())
    with ml:
        if key in _cache:
            return _cache[key]
        with _lock:
            if "f" not in _form_cache:  # the blank page's form state is valid for any postback
                b, r = http.soup(LISTING)
                _form_cache["f"] = (r.url, _form(b))
            post_url, form = _form_cache["f"]
        data = dict(form)
        data.update({"__EVENTTARGET": P + "ddlMinistry", "__EVENTARGUMENT": "",
                     P + "ddlMinistry": ministry_id, P + "ddlday": "0",
                     P + "ddlMonth": str(m), P + "ddlYear": str(y)})
        resp = http.post(post_url, data=data, headers={"Referer": post_url})
        resp.raise_for_status()
        page = BeautifulSoup(http.text(resp), "lxml")
        area = page.select_one(".content-area")
        if area is None:
            raise RuntimeError("PIB listing layout changed (no .content-area)")
        rows: list[tuple[date | None, str, str]] = []
        want = " ".join(ministry_name.lower().split())
        for h in area.find_all("h3"):
            if " ".join(h.get_text(" ", strip=True).lower().split()) != want:
                continue
            ul = h.find_next_sibling("ul")
            if ul is None:
                continue
            for li in ul.find_all("li"):
                a = li.find("a", href=re.compile(r"PRID=\d+", re.I))
                if not a:
                    continue
                span = li.find("span", class_="publishdatesmall")
                d = parse_date(re.sub(r"(?i)posted\s*on\s*:?", "", span.get_text(" ", strip=True))) if span else None
                title = " ".join((a.get("title") or a.get_text(" ", strip=True)).split())
                prid = re.search(r"PRID=(\d+)", a["href"], re.I).group(1)
                rows.append((d, title, f"{BASE}PressReleaseDetail.aspx?PRID={prid}"))
        _cache[key] = rows
        return rows


def _months(since: date):
    y, m = date.today().year, date.today().month
    for _ in range(MAX_MONTHS):
        yield y, m
        if (y, m) <= (since.year, since.month):
            return
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)


class _PibMinistry(Source):
    ministry_id = ""
    ministry_name = ""
    doc_type = "Press Release"
    homepage = LISTING
    title_rx: re.Pattern | None = None       # keep only matching titles (None = all)
    exclude_rx: re.Pattern | None = None

    def fetch(self, since: date):
        items, seen = [], set()
        for y, m in _months(since):
            for d, title, url in _month_rows(self.ministry_id, self.ministry_name, y, m):
                if url in seen or (d and d < since):
                    continue
                if self.title_rx and not self.title_rx.search(title):
                    continue
                if self.exclude_rx and self.exclude_rx.search(title):
                    continue
                seen.add(url)
                items.append(self.item(title=title, date=d, url=url, doc_type=self.doc_type))
        return items


class PibMca(_PibMinistry):
    id, regulator, group = "pib_mca", "MCA", "MCA"
    name = "PIB - Ministry of Corporate Affairs press releases"
    ministry_id, ministry_name = "66", "Ministry of Corporate Affairs"


class PibMeity(_PibMinistry):
    id, regulator, group = "pib_meity", "MeitY", "Data Protection"
    name = "PIB - Ministry of Electronics & IT press releases"
    ministry_id, ministry_name = "1323", "Ministry of Electronics & IT"


class PibFinance(_PibMinistry):
    id, regulator, group = "pib_finance", "MoF", "Direct Tax"
    name = "PIB - Ministry of Finance press releases (all)"
    ministry_id, ministry_name = "15", "Ministry of Finance"
    enabled_by_default = False  # ~70+/month, mostly DRI/CBN enforcement news


class PibCbdt(PibFinance):
    id, regulator, group = "pib_cbdt", "CBDT", "Direct Tax"
    name = "PIB - CBDT / Income Tax press releases"
    enabled_by_default = True
    title_rx = re.compile(
        r"\bCBDT\b|Central Board of Direct Taxes|Income[\s-]?Tax|\bDirect Tax|\bITRs?\b|\bTDS\b|\bTCS\b"
        r"|Advance Tax|Tax Return|Faceless Assessment|Vivad se Vishwas|(?-i:\bPAN\b)|Tax Payers?|Taxpayers?",
        re.I)
    exclude_rx = re.compile(r"\bseiz|smuggl|arrest|\bbust|\bfoil|narcotic|\bdrugs?\b", re.I)


class PibCbic(PibFinance):
    id, regulator, group = "pib_cbic", "CBIC", "GST & Customs"
    name = "PIB - CBIC / GST / Customs policy press releases"
    enabled_by_default = True
    title_rx = re.compile(
        r"\bCBIC\b|Central Board of Indirect Taxes|\bGST\b|Goods and Services Tax|Customs (?:Duty|Tariff|Act)"
        r"|Indirect Tax|Excise Duty|Export Duty|Import Duty|Trade Facilitation",
        re.I)
    exclude_rx = re.compile(r"\bseiz|smuggl|arrest|\bbust|\bfoil|narcotic|\bdrugs?\b", re.I)


SOURCES = (PibMca, PibMeity, PibFinance, PibCbdt, PibCbic)
