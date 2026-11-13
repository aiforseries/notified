"""Partner / client digest: an A4 PDF of a period's regulatory updates, laid out like the app's register.

Cover: title, a plain-language lead paragraph, items your team flagged, deadlines and extensions, and a
contents list with page numbers. Body: one section per topic; each entry shows date and regulator (with the
regulator's file-tab colour), the title in a serif, and a quiet line with type, reference and PDF link.
"""
from __future__ import annotations

import logging
import os
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path

from fpdf import FPDF

from .palette import FILE_TABS
from .paths import resource

log = logging.getLogger("regwatch")
logging.getLogger("fontTools").setLevel(logging.ERROR)  # silence "MERG NOT subset" noise

TOPIC_ORDER = [
    "Audit & Assurance", "Accounting Standards / Ind AS", "Companies Act / LLP", "Income Tax / TDS", "GST",
    "Customs & Foreign Trade", "SEBI / Listing", "Banking / NBFC / FEMA", "Labour & Payroll",
    "Data Protection / IT", "Insolvency & Valuation", "IFSC / GIFT City", "Insurance & Pension",
    "Competition Law", "Sustainability / BRSR",
]
DEADLINE_TAG = "Deadline / Extension"
FLAG_TAGS = {DEADLINE_TAG, "Posted late"}
DEFAULT_EXCLUDE = ("Students / Exams", "Internal / Admin", "Enforcement news", "Events & CPE")
OTHER = "Other updates"
DISCLAIMER = "Compiled from official regulator websites by Notified. Refer to the official document before acting."
ACTION = "Relevant - action needed"

# print palette: the app's ledger ink on white paper
INK = (0x1D, 0x2A, 0x33)
INK2 = (0x56, 0x64, 0x6C)
INK3 = (0x8A, 0x96, 0x9B)
RULE = (0xDD, 0xE1, 0xDA)
RULE_STRONG = (0xB9, 0xC1, 0xB6)
GREEN = (0x1E, 0x6B, 0x52)
VIOLET = (0x5B, 0x3E, 0x96)
AMBER = (0x8A, 0x5A, 0x12)
DANGER = (0xA3, 0x3A, 0x2C)
WASH = (0xF3, 0xF4, 0xF0)

FONTS = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
ASCII_SUBS = {"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-", "…": "...", "₹": "Rs.",
              "•": "-", "\u00a0": " ", "\u200b": "", "\ufeff": ""}


def _hex(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def reg_colour(reg: str) -> tuple[int, int, int]:
    t = FILE_TABS.get(reg)
    return _hex(t[0]) if t else INK3


def long_date(d: date) -> str:
    return f"{d.day} {d:%B %Y}"


def short_date(d: date | None) -> str:
    return f"{d.day} {d:%b}" if d else "Undated"


def period_text(since: date, until: date) -> str:
    if since.year == until.year:
        if since.month == until.month:
            return f"{since.day} – {until.day} {until:%B %Y}"
        return f"{since.day} {since:%B} – {until.day} {until:%B %Y}"
    return f"{long_date(since)} – {long_date(until)}"


def _in_period(r: dict, since: date, until: date) -> bool:
    d = r.get("date")
    if d:
        return since <= d <= until
    fs = r.get("first_seen")
    if isinstance(fs, str):
        try:
            fs = datetime.fromisoformat(fs)
        except ValueError:
            fs = None
    return bool(fs) and since <= fs.date() <= until


def _topic(tags: list[str]) -> str:
    pro = [t for t in tags if t not in FLAG_TAGS]
    for t in pro:
        if t in TOPIC_ORDER:
            return t
    return pro[0] if pro else OTHER


PROSE = {"Banking / NBFC / FEMA": "banking, NBFC and FEMA", "SEBI / Listing": "SEBI and listing",
         "Income Tax / TDS": "income tax and TDS", "Companies Act / LLP": "company and LLP law",
         "Accounting Standards / Ind AS": "accounting standards", "Customs & Foreign Trade": "customs and foreign trade",
         "Labour & Payroll": "labour and payroll", "Data Protection / IT": "data protection and IT",
         "Insolvency & Valuation": "insolvency and valuation", "IFSC / GIFT City": "IFSC (GIFT City)",
         "Insurance & Pension": "insurance and pensions", "Sustainability / BRSR": "sustainability reporting",
         "Audit & Assurance": "audit and assurance", "Competition Law": "competition law", "GST": "GST"}


def prose(topic: str) -> str:
    return PROSE.get(topic, topic.lower())


def _words(n: int) -> str:
    small = ["no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"]
    return small[n] if 0 <= n < len(small) else f"{n:,}"


class _Doc(FPDF):
    def __init__(self, running_head: str):
        super().__init__(format="A4", unit="mm")
        self.running_head = running_head
        self.set_margins(20, 18, 20)
        self.set_auto_page_break(False)
        self.c_margin = 0
        self.glyphs: set[int] = set()
        self.serif = self.sans = self.sans_sb = "helvetica"
        self._load_fonts()

    # ---------- fonts ----------
    def _add(self, family: str, style: str, path: Path, **kw) -> bool:
        if not path.exists():
            return False
        try:
            self.add_font(family, style, str(path), **kw)
            self.glyphs |= set(self.fonts[family.lower() + style.upper()].cmap)
            return True
        except Exception as e:  # noqa: BLE001 - a broken font file must not stop the digest
            log.warning("font %s: %s", path, e)
            return False

    def _load_fonts(self):
        if self._add("sans", "", FONTS / "segoeui.ttf"):
            self._add("sans", "B", FONTS / "segoeuib.ttf") or self._add("sans", "B", FONTS / "segoeui.ttf")
            self.sans = "sans"
            self.sans_sb = "sanssb" if self._add("sanssb", "", FONTS / "seguisb.ttf") else "sans"
        elif self._add("sans", "", FONTS / "arial.ttf"):
            self._add("sans", "B", FONTS / "arialbd.ttf")
            self.sans = self.sans_sb = "sans"
        # serif for titles: Sitka (Windows 8.1+), then Cambria, then Georgia
        if self._add("serif", "", FONTS / "SitkaVF.ttf", variations={"wght": 400}):
            self._add("serif", "B", FONTS / "SitkaVF.ttf", variations={"wght": 620})
            self.serif = "serif"
        elif self._add("serif", "", FONTS / "cambria.ttc", collection_font_number=0):
            self._add("serif", "B", FONTS / "cambriab.ttf")
            self.serif = "serif"
        elif self._add("serif", "", FONTS / "georgia.ttf"):
            self._add("serif", "B", FONTS / "georgiab.ttf")
            self.serif = "serif"
        else:
            self.serif = self.sans
        fallbacks = [f for f in ("sans",) if self.sans == "sans"]
        for name in ("NirmalaUI.ttf", "Nirmala.ttf", "Nirmala.ttc"):
            if self._add("indic", "", FONTS / name):
                fallbacks.append("indic")
                break
        if self._add("symbols", "", FONTS / "seguisym.ttf"):
            fallbacks.append("symbols")
        if fallbacks:
            self.set_fallback_fonts(fallbacks, exact_match=False)
        if self.sans == "helvetica":
            self.glyphs = set(range(32, 256))
        try:  # proper Devanagari shaping needs HarfBuzz; optional
            import uharfbuzz  # noqa: F401
            self.set_text_shaping(True)
        except Exception:  # noqa: BLE001
            pass

    def safe(self, s: str) -> str:
        """Swap characters no loaded font can draw, so output never shows boxes or crashes."""
        s = " ".join((s or "").split())
        out = []
        for ch in s:
            if ord(ch) in self.glyphs:
                out.append(ch)
            elif ch in ASCII_SUBS:
                out.append(ASCII_SUBS[ch])
            else:
                plain = unicodedata.normalize("NFKD", ch).encode("ascii", "ignore").decode()
                out.append(plain or ("" if unicodedata.category(ch).startswith("M") else "?"))
        return "".join(out)

    def use(self, kind: str, size: float, color=INK, bold: bool = False):
        fam = {"serif": self.serif, "sans": self.sans, "sb": self.sans_sb}[kind]
        style = "B" if bold and kind != "sb" else ""
        self.set_font(fam, style, size)
        self.set_text_color(*color)

    # ---------- page furniture ----------
    def header(self):
        if self.page_no() == 1:
            return
        self.use("sans", 7.5, INK3)
        self.set_xy(self.l_margin, 10)
        self.cell(self.epw / 2, 4, self.safe(self.running_head))
        self.cell(self.epw / 2, 4, "Notified", align="R")
        self.hline(15.2, RULE)

    def footer(self):
        y = self.h - 13
        self.hline(y - 1.6, RULE)
        self.set_xy(self.l_margin, y)
        self.use("sans", 7, INK3)
        self.cell(self.epw - 24, 4, self.safe(DISCLAIMER))
        self.cell(24, 4, f"Page {self.page_no()} of {{nb}}", align="R")

    def hline(self, y: float, color=RULE, width: float = 0.2, x1: float | None = None, x2: float | None = None):
        self.set_draw_color(*color)
        self.set_line_width(width)
        self.line(self.l_margin if x1 is None else x1, y, (self.w - self.r_margin) if x2 is None else x2, y)

    @property
    def bottom(self) -> float:
        return self.h - 20

    def text_height(self, kind: str, size: float, w: float, lh: float, text: str, bold: bool = False) -> float:
        self.use(kind, size, bold=bold)
        return self.multi_cell(w, lh, self.safe(text), dry_run=True, output="HEIGHT", align="L")


# ---------------------------------------------------------------------------------------------------------
def build_digest(rows: list[dict], since: date, until: date, out_path: Path, *, title: str = "Regulatory Update",
                 subtitle: str = "", exclude_tags=DEFAULT_EXCLUDE, include_regulators: list[str] | None = None,
                 user_cols: dict | None = None, rules_file: Path | None = None) -> Path:
    """Write the digest PDF. `rows` are Store.all_rows() dicts; `user_cols` maps uid -> {Status, Owner, Notes}
    (team flags and notes); `rules_file` re-applies the user's current topic rules."""
    rows = [dict(r) for r in rows]
    for r in rows:  # titles stored before v0.3.1 may still end in a file name
        r["title"] = re.sub(r"\.pdf\s*$", "", r.get("title") or "", flags=re.I).strip()
    if rules_file is not None:
        from .tagger import Tagger
        Tagger(rules_file).retag(rows)
    if not any("member_uids" in r for r in rows):
        try:
            from .dedupe import merge_duplicates
            rows = merge_duplicates(rows)
        except ImportError:
            pass
    exclude = set(exclude_tags or ())
    regs = set(include_regulators) if include_regulators else None
    items = [r for r in rows if _in_period(r, since, until) and not exclude.intersection(r.get("tags") or [])
             and (regs is None or r.get("regulator") in regs)]

    def key(r: dict):
        fs = r.get("first_seen")
        return r.get("date") or (fs.date() if isinstance(fs, datetime) else date.min)
    items.sort(key=key, reverse=True)

    user_cols = user_cols or {}

    def user(r: dict) -> dict:
        for u in r.get("member_uids") or [r.get("uid")]:
            if user_cols.get(u):
                return user_cols[u]
        return {}

    groups: dict[str, list[dict]] = defaultdict(list)
    for r in items:
        groups[_topic(r.get("tags") or [])].append(r)
    extra = sorted((t for t in groups if t not in TOPIC_ORDER and t != OTHER), key=lambda t: -len(groups[t]))
    order = [t for t in TOPIC_ORDER if t in groups] + extra + ([OTHER] if OTHER in groups else [])
    deadlines = [r for r in items if DEADLINE_TAG in (r.get("tags") or [])]
    flagged = [r for r in items if user(r).get("Status") == ACTION or (user(r).get("Notes") or "").strip()]
    flagged.sort(key=lambda r: user(r).get("Status") != ACTION)

    ctx = dict(items=items, groups=groups, order=order, deadlines=deadlines, flagged=flagged, user=user,
               since=since, until=until, title=title, subtitle=subtitle)
    # two passes: the first finds the page each section starts on, the second prints the contents with them
    pages = _render(ctx, None).section_pages
    pdf = _render(ctx, pages)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(out_path))
    return out_path


def _render(c: dict, toc_pages: dict | None) -> _Doc:
    items, groups, order = c["items"], c["groups"], c["order"]
    period = period_text(c["since"], c["until"])
    pdf = _Doc(f"{c['title']}   {period}")
    pdf.section_pages = {}
    pdf.set_title(f"{c['title']} – {period}")
    pdf.set_author("Notified")
    pdf.set_creator("Notified")
    pdf.alias_nb_pages()
    pdf.add_page()
    L, W = pdf.l_margin, pdf.epw

    def ensure(h: float) -> float:
        if pdf.get_y() + h > pdf.bottom:
            pdf.add_page()
            pdf.set_y(22)
        return pdf.get_y()

    # ---------- masthead ----------
    logo = resource("assets/regwatch.png")
    if logo.exists():
        pdf.image(str(logo), x=L, y=16, w=7, h=7)
    pdf.set_xy(L + 9, 16.6)
    pdf.use("sb", 9.5, INK)
    pdf.cell(40, 6, "Notified")
    pdf.set_xy(L, 16.6)
    pdf.use("sans", 8, INK3)
    pdf.cell(W, 6, f"Generated {long_date(date.today())}", align="R")
    pdf.hline(27, INK, 0.5)

    pdf.set_xy(L, 34)
    pdf.use("serif", 27, INK, bold=True)
    pdf.multi_cell(W, 11, pdf.safe(c["title"]), align="L", new_x="LMARGIN", new_y="NEXT")
    pdf.set_x(L)
    pdf.use("sans", 11, INK2)
    pdf.cell(W, 6, pdf.safe(period + (f"   {c['subtitle']}" if c["subtitle"] else "")), new_x="LMARGIN", new_y="NEXT")

    # ---------- lead paragraph ----------
    n = len(items)
    by_reg = Counter(r.get("regulator") or "Other" for r in items)
    if n:
        top = [t for t in sorted(order, key=lambda t: -len(groups[t])) if t != OTHER][:2]
        lead = (f"This digest covers {n:,} update{'s' if n != 1 else ''} from {len(by_reg)} "
                f"regulator{'s' if len(by_reg) != 1 else ''}, published between {long_date(c['since'])} and "
                f"{long_date(c['until'])}.")
        if top:
            lead += " The busiest area" + (f"s were {prose(top[0])} ({len(groups[top[0]])}) and {prose(top[1])} "
                                            f"({len(groups[top[1]])})." if len(top) > 1
                                            else f" was {prose(top[0])} ({len(groups[top[0]])}).")
        nd = len(c["deadlines"])
        if nd:
            lead += f" {_words(nd).capitalize()} {'notice changes' if nd == 1 else 'notices change'} a deadline or timeline."
        nf = len(c["flagged"])
        if nf:
            lead += f" Your team has flagged {_words(nf)} for attention."
    else:
        lead = f"No regulatory updates were found between {long_date(c['since'])} and {long_date(c['until'])}."
    pdf.set_xy(L, pdf.get_y() + 7)
    pdf.use("serif", 12.5, INK)
    pdf.multi_cell(min(W, 150), 6.4, pdf.safe(lead), align="L", new_x="LMARGIN", new_y="NEXT")
    pdf.set_y(pdf.get_y() + 2)
    pdf.use("sans", 8.5, INK2)
    pdf.set_x(L)
    if by_reg:
        pdf.multi_cell(W, 4.6, pdf.safe("By regulator: " + ", ".join(f"{k} {v}" for k, v in by_reg.most_common())),
                       align="L", new_x="LMARGIN", new_y="NEXT")

    # ---------- entry renderer (shared by cover lists and body) ----------
    DATE_W, GAP = 24, 4
    TX = L + DATE_W + GAP
    TW = W - DATE_W - GAP

    def meta_line(r: dict) -> list[tuple[str, tuple, str]]:
        """[(text, colour, link)] pieces for the quiet line under a title."""
        dt, ref = (r.get("doc_type") or "").strip(), (r.get("ref_no") or "").strip()
        if dt and ref.lower().startswith(dt.lower()):
            dt = ""
        parts = [(p, INK2, "") for p in (dt, ref) if p]
        if DEADLINE_TAG in (r.get("tags") or []):
            parts.append(("Deadline or extension", AMBER, ""))
        also = [a.get("regulator") or a.get("source_id", "") for a in r.get("also") or []]
        if also:
            parts.append(("Also from " + ", ".join(dict.fromkeys(also)), INK3, ""))
        if r.get("pdf_url"):
            parts.append(("PDF", GREEN, r["pdf_url"]))
        return parts

    def entry_height(r: dict, with_note: bool) -> float:
        h = pdf.text_height("serif", 10.4, TW, 5.0, r.get("title", ""))
        h += 4.6  # meta line
        note = (c["user"](r).get("Notes") or "").strip() if with_note else ""
        if note:
            h += 1.2 + pdf.text_height("sans", 8.4, TW, 4.2, "Note: " + note)
        return max(h, 9.5) + 5.0

    def entry(r: dict, with_note: bool = False, stamp: str = ""):
        h = entry_height(r, with_note)
        y = ensure(h)
        col = reg_colour(r.get("regulator", ""))
        pdf.set_fill_color(*col)
        pdf.rect(L, y + 0.6, 0.9, h - 5.4, style="F")
        pdf.set_xy(L + 3.2, y)
        pdf.use("sb", 8.6, INK)
        pdf.cell(DATE_W - 3, 4.8, short_date(r.get("date")))
        pdf.set_xy(L + 3.2, y + 4.4)
        pdf.use("sb", 8, col)
        pdf.cell(DATE_W - 3, 4.4, pdf.safe(r.get("regulator", "")))
        if stamp:
            pdf.set_xy(L + 3.2, y + 9.2)
            pdf.use("sb", 7, DANGER)
            pdf.set_draw_color(*DANGER)
            pdf.set_line_width(0.35)
            sw = pdf.get_string_width(stamp) + 3
            pdf.rect(L + 3.2, y + 9.4, sw, 4, style="D", round_corners=True, corner_radius=0.8)
            pdf.cell(sw, 4.4, stamp, align="C")
        pdf.set_xy(TX, y)
        pdf.use("serif", 10.4, INK)
        pdf.multi_cell(TW, 5.0, pdf.safe(r.get("title", "")), link=r.get("url") or "", align="L", new_x="LEFT", new_y="NEXT")
        x, my = TX, pdf.get_y() + 0.6
        for i, (text, colr, link) in enumerate(meta_line(r)):
            text = pdf.safe(text)
            pdf.use("sb" if link else "sans", 7.9, colr)
            w = pdf.get_string_width(text)
            if x + w > TX + TW:
                break
            pdf.set_xy(x, my)
            pdf.cell(w, 4, text, link=link)
            if link:
                pdf.hline(my + 3.6, colr, 0.2, x, x + w)
            x += w + 4.5
        y2 = my + 4.6
        note = (c["user"](r).get("Notes") or "").strip() if with_note else ""
        if note:
            owner = (c["user"](r).get("Owner") or "").strip()
            pdf.set_xy(TX, y2 + 1.0)
            pdf.use("sans", 8.4, VIOLET)
            pdf.multi_cell(TW, 4.2, pdf.safe(f"Note{(' from ' + owner) if owner else ''}: {note}"),
                           align="L", new_x="LEFT", new_y="NEXT")
            y2 = pdf.get_y()
        y_end = max(y2, y + 9.5) + 2.4
        pdf.hline(y_end, RULE, 0.15, TX, L + W)
        pdf.set_y(y_end + 2.6)

    def compact(r: dict):
        """One line per item: for the low-priority 'Other updates' list."""
        h = pdf.text_height("sans", 8.8, TW, 4.4, r.get("title", "")) + 2.6
        y = ensure(h)
        pdf.set_xy(L + 3.2, y)
        pdf.use("sans", 8, INK2)
        pdf.cell(DATE_W - 3, 4.4, f"{short_date(r.get('date'))}  {pdf.safe(r.get('regulator', ''))}")
        pdf.set_xy(TX, y)
        pdf.use("sans", 8.8, INK)
        pdf.multi_cell(TW, 4.4, pdf.safe(r.get("title", "")), link=r.get("url") or "", align="L",
                       new_x="LEFT", new_y="NEXT")
        pdf.set_y(pdf.get_y() + 2.6)

    def heading(text: str, right: str = "", size: float = 15, before: float = 9):
        y = ensure(before + 12 + 14)
        pdf.set_y(y + before)
        pdf.set_x(L)
        pdf.use("serif", size, INK, bold=True)
        pdf.cell(W * 0.75, 8, pdf.safe(text))
        if right:
            pdf.use("sans", 8.5, INK3)
            pdf.cell(W * 0.25, 8, pdf.safe(right), align="R")
        pdf.hline(pdf.get_y() + 9.5, INK, 0.45)
        pdf.set_y(pdf.get_y() + 12.5)

    # ---------- contents ----------
    if items:
        rows_h = 6.4 * len(order)
        ensure(8 + 12.5 + rows_h)
        heading("Contents", size=13, before=8)
        for t in order:
            y = pdf.get_y()
            pg = (toc_pages or {}).get(t)
            link = pdf.add_link(page=pg) if pg else ""
            pdf.set_xy(L, y)
            pdf.use("sans", 10, INK)
            name = pdf.safe(t)
            nw = pdf.get_string_width(name)
            pdf.cell(nw, 6, name, link=link)
            cnt = f"{len(groups[t])} update{'s' if len(groups[t]) != 1 else ''}"
            pdf.use("sans", 8.5, INK3)
            cw = pdf.get_string_width(cnt)
            pg_txt = f"page {pg}" if pg else ""
            pw = 16
            # dotted leader between name and count
            x1, x2 = L + nw + 3, L + W - pw - cw - 4
            pdf.set_draw_color(*RULE_STRONG)
            pdf.set_line_width(0.3)
            pdf.set_dash_pattern(dash=0.3, gap=1.2)
            if x2 > x1:
                pdf.line(x1, y + 4.2, x2, y + 4.2)
            pdf.set_dash_pattern()
            pdf.set_xy(L + W - pw - cw - 1, y)
            pdf.cell(cw, 6, cnt)
            pdf.set_xy(L + W - pw, y)
            pdf.use("sb", 8.5, GREEN if pg else INK3)
            pdf.cell(pw, 6, pg_txt, align="R", link=link)
            pdf.set_y(y + 6.4)

    # ---------- flagged by the team ----------
    if c["flagged"]:
        heading("Flagged by your team", f"{len(c['flagged'])}", size=13, before=8)
        for r in c["flagged"][:12]:
            entry(r, with_note=True, stamp="Action" if c["user"](r).get("Status") == ACTION else "")

    # ---------- deadlines ----------
    if c["deadlines"]:
        heading("Deadlines and extensions", f"{len(c['deadlines'])}", size=13, before=8)
        for r in c["deadlines"][:10]:
            entry(r)
        if len(c["deadlines"]) > 10:
            pdf.set_x(TX)
            pdf.use("sans", 8.4, INK3)
            pdf.cell(TW, 5, f"{len(c['deadlines']) - 10} more appear in the sections below.")
            pdf.set_y(pdf.get_y() + 6)

    # ---------- body ----------
    for t in order:
        rs = groups[t]
        # start each topic on a fresh line block; keep heading with its first entry
        need = 9 + 12.5 + entry_height(rs[0], False)
        if pdf.get_y() + need > pdf.bottom:
            pdf.add_page()
            pdf.set_y(22)
        pdf.section_pages[t] = pdf.page_no()
        pdf.start_section(pdf.safe(t))
        heading(t, f"{len(rs)} update{'s' if len(rs) != 1 else ''}")
        if t == OTHER:  # general news without a professional topic: keep it brief
            pdf.set_x(L)
            pdf.use("sans", 8.4, INK3)
            pdf.multi_cell(W, 4.4, "Government and regulator news that did not match a professional topic.", align="L",
                           new_x="LMARGIN", new_y="NEXT")
            pdf.set_y(pdf.get_y() + 2.5)
            for r in rs:
                compact(r)
            continue
        for r in rs:
            entry(r)
    return pdf
