"""Partner / client digest: a short, polished A4 PDF of the updates in a period, grouped by topic."""
from __future__ import annotations

import logging
import os
import unicodedata
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path

from fpdf import FPDF
from fpdf.enums import XPos, YPos

from .paths import resource

log = logging.getLogger("regwatch")
logging.getLogger("fontTools").setLevel(logging.ERROR)  # silence "MERG NOT subset" noise

TOPIC_ORDER = [
    "Audit & Assurance", "Accounting Standards / Ind AS", "Companies Act / LLP", "Income Tax / TDS", "GST",
    "Customs & Foreign Trade", "SEBI / Listing", "Banking / NBFC / FEMA", "Labour & Payroll",
    "Data Protection / IT", "Insolvency & Valuation", "Sustainability / BRSR",
]
DEADLINE_TAG = "Deadline / Extension"
META_TAGS = {DEADLINE_TAG, "Posted late"}   # flags, not topics
OTHER = "Other updates"
DISCLAIMER = "Compiled from official regulator websites by RegWatch. Refer to the official document before acting."

ACCENT = (0x0F, 0x76, 0x6E)
ACCENT_SOFT = (0xE6, 0xF2, 0xF1)
INK = (0x0F, 0x17, 0x2A)
MUTED = (0x64, 0x74, 0x8B)
RULE = (0xE2, 0xE8, 0xF0)
BAR_BG = (0xF1, 0xF5, 0xF9)

FONTS = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
# Characters to swap for something plain when no loaded font has the glyph.
ASCII_SUBS = {"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-", "…": "...", "₹": "Rs.",
              "•": "-", " ": " ", "​": "", "﻿": ""}


def fmt_date(d: date | None) -> str:
    return f"{d.day} {d:%b %Y}" if d else "Undated"


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


def _topic(tags: list[str], exclude: set[str]) -> str:
    pro = [t for t in tags if t not in META_TAGS and t not in exclude]
    for t in pro:                       # first standard professional topic, in the item's own order
        if t in TOPIC_ORDER:
            return t
    return pro[0] if pro else OTHER     # e.g. "Enforcement / Orders" or a user-defined tag


class _Doc(FPDF):
    def __init__(self, header_text: str):
        super().__init__(format="A4", unit="mm")
        self.header_text = header_text
        self.set_margins(18, 16, 18)
        self.set_auto_page_break(True, margin=20)
        self.glyphs: set[int] = set()
        self._load_fonts()

    # ---- fonts ---------------------------------------------------------------------------------------------
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
        regular = next((FONTS / f for f in ("segoeui.ttf", "arial.ttf") if (FONTS / f).exists()), None)
        if regular:
            bold = FONTS / ("segoeuib.ttf" if regular.name == "segoeui.ttf" else "arialbd.ttf")
            sb = FONTS / "seguisb.ttf"
            self._add("body", "", regular)
            self._add("body", "B", bold if bold.exists() else regular)
            self.family = "body"
            self.semibold = "body"
            if sb.exists() and self._add("bodysb", "", sb):
                self.semibold = "bodysb"
            fallbacks = []
            for name, idx in (("NirmalaUI.ttf", 0), ("Nirmala.ttf", 0), ("Nirmala.ttc", 0)):
                if self._add("indic", "", FONTS / name, collection_font_number=idx):
                    fallbacks.append("indic")
                    break
            if self._add("symbols", "", FONTS / "seguisym.ttf"):
                fallbacks.append("symbols")
            if fallbacks:
                self.set_fallback_fonts(fallbacks, exact_match=False)
            try:  # proper Devanagari conjuncts/matras need HarfBuzz; optional
                import uharfbuzz  # noqa: F401
                self.set_text_shaping(True)
            except Exception:  # noqa: BLE001
                pass
        else:  # no Windows fonts at all: core font, latin-1 only
            self.family = self.semibold = "helvetica"
            self.glyphs = set(range(32, 256))

    def safe(self, s: str) -> str:
        """Replace characters no loaded font can draw, so output never crashes or shows tofu boxes."""
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

    def font(self, size: float, style: str = "", color=INK, semibold: bool = False):
        self.set_font(self.semibold if semibold else self.family, "" if semibold else style, size)
        self.set_text_color(*color)

    # ---- page furniture ------------------------------------------------------------------------------------
    def header(self):
        if self.page_no() == 1:
            return
        self.font(7.5, color=MUTED)
        self.set_xy(self.l_margin, 9)
        self.cell(0, 4, self.safe(self.header_text), align="L")
        self.set_draw_color(*RULE)
        self.set_line_width(0.2)
        self.line(self.l_margin, 14, self.w - self.r_margin, 14)
        self.set_y(18)

    def footer(self):
        self.set_y(-13)
        self.set_draw_color(*RULE)
        self.set_line_width(0.2)
        self.line(self.l_margin, self.get_y() - 1.5, self.w - self.r_margin, self.get_y() - 1.5)
        self.font(7, color=MUTED)
        self.cell(self.epw - 25, 4, self.safe(DISCLAIMER), align="L")
        self.cell(25, 4, f"Page {self.page_no()} of {{nb}}", align="R")

    def pill(self, text: str, x: float, y: float, h: float = 4.2) -> float:
        self.font(6.8, semibold=True, color=ACCENT)
        text = self.safe(text)
        w = self.get_string_width(text) + 3.6
        self.set_fill_color(*ACCENT_SOFT)
        self.rect(x, y, w, h, style="F", round_corners=True, corner_radius=1.2)
        self.set_xy(x, y)
        self.cell(w, h, text, align="C")
        return w


def build_digest(rows: list[dict], since: date, until: date, out_path: Path, *, title: str = "Regulatory Update",
                 subtitle: str = "", exclude_tags=("Students / Exams", "Internal / Admin"),
                 include_regulators: list[str] | None = None) -> Path:
    """Write an A4 PDF digest of `rows` (Store.all_rows() dicts) dated since..until; returns out_path."""
    exclude = set(exclude_tags or ())
    regs = set(include_regulators) if include_regulators else None
    items = [r for r in rows if _in_period(r, since, until)
             and not exclude.intersection(r.get("tags") or [])
             and (regs is None or r.get("regulator") in regs)]
    def sort_key(r: dict) -> date:
        fs = r.get("first_seen")
        return r.get("date") or (fs.date() if isinstance(fs, datetime) else date.min)
    items.sort(key=sort_key, reverse=True)

    groups: dict[str, list[dict]] = defaultdict(list)
    for r in items:
        groups[_topic(r.get("tags") or [], exclude)].append(r)
    extra = sorted((t for t in groups if t not in TOPIC_ORDER and t != OTHER), key=lambda t: -len(groups[t]))
    order = [t for t in TOPIC_ORDER if t in groups] + extra + ([OTHER] if OTHER in groups else [])
    deadlines = [r for r in items if DEADLINE_TAG in (r.get("tags") or [])]
    by_reg = Counter(r.get("regulator") or "Other" for r in items)
    period = f"{fmt_date(since)} – {fmt_date(until)}"

    pdf = _Doc(f"{title}  ·  {period}")
    pdf.set_title(f"{title} – {period}")
    pdf.set_author("RegWatch")
    pdf.set_creator("RegWatch")
    pdf.alias_nb_pages()
    pdf.add_page()
    L, W = pdf.l_margin, pdf.epw

    # ---- header block --------------------------------------------------------------------------------------
    pdf.set_fill_color(*ACCENT)
    pdf.rect(0, 0, pdf.w, 3, style="F")
    y0 = 16
    logo = resource("assets/regwatch.png")
    if logo.exists():
        pdf.image(str(logo), x=L, y=y0, w=13, h=13)
    pdf.set_xy(L + 17, y0 - 0.5)
    pdf.font(7.5, semibold=True, color=ACCENT)
    pdf.cell(0, 4, "REGWATCH  ·  INDIAN REGULATORY TRACKER")
    pdf.set_xy(L + 17, y0 + 3.6)
    pdf.font(21, "B")
    pdf.cell(0, 10, pdf.safe(title))
    y = y0 + 17
    if subtitle:
        pdf.set_xy(L, y)
        pdf.font(10.5, color=MUTED)
        pdf.multi_cell(W, 5.2, pdf.safe(subtitle))
        y = pdf.get_y() + 1
    pdf.set_xy(L, y + 1)
    pdf.font(11, semibold=True)
    pdf.cell(pdf.get_string_width(period) + 1, 6, pdf.safe(period))
    pdf.font(8.5, color=MUTED)
    pdf.cell(0, 6, f"     Generated {fmt_date(date.today())}")
    y = pdf.get_y() + 9

    # stat tiles
    stats = [(str(len(items)), "updates"), (str(len(by_reg)), "regulators"),
             (str(len(order)), "topics"), (str(len(deadlines)), "deadlines / extensions")]
    tw = (W - 3 * 4) / 4
    for i, (n, lab) in enumerate(stats):
        x = L + i * (tw + 4)
        pdf.set_fill_color(*BAR_BG)
        pdf.rect(x, y, tw, 17, style="F", round_corners=True, corner_radius=1.8)
        pdf.set_xy(x + 4, y + 2.2)
        pdf.font(17, "B", color=ACCENT if i == 0 else INK)
        pdf.cell(tw - 8, 8, n)
        pdf.set_xy(x + 4, y + 10.2)
        pdf.font(7.5, color=MUTED)
        pdf.cell(tw - 8, 4, lab)
    y += 21
    pdf.set_xy(L, y)
    pdf.font(7.8, color=MUTED)
    pdf.multi_cell(W, 4.2, pdf.safe("By regulator:  " + "   ·   ".join(f"{k} {v}" for k, v in by_reg.most_common())))
    y = pdf.get_y() + 6

    if not items:
        pdf.set_xy(L, y)
        pdf.font(11, color=MUTED)
        pdf.multi_cell(W, 6, "No updates were found for this period.")
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        pdf.output(str(out_path))
        return out_path

    def section_title(text: str, y: float, right: str = "") -> float:
        pdf.set_xy(L, y)
        pdf.font(8, semibold=True, color=ACCENT)
        pdf.cell(W / 2, 5, pdf.safe(text.upper()))
        if right:
            pdf.font(7.5, color=MUTED)
            pdf.cell(W / 2, 5, pdf.safe(right), align="R")
        pdf.set_draw_color(*ACCENT)
        pdf.set_line_width(0.35)
        pdf.line(L, y + 6, L + W, y + 6)
        return y + 9

    # ---- at a glance: topic bars in two columns ----------------------------------------------------------
    y = section_title("At a glance", y, "updates by topic")
    colw, gap, rh = (W - 10) / 2, 10, 6.4
    labw, numw = 46, 8
    top = max(len(groups[t]) for t in order)
    half = (len(order) + 1) // 2
    for i, t in enumerate(order):
        col, row = divmod(i, half)
        x, yy = L + col * (colw + gap), y + row * rh
        pdf.set_xy(x, yy)
        pdf.font(8.2)
        pdf.cell(labw, rh - 1.4, pdf.safe(t))
        bx, bw = x + labw + 1, colw - labw - numw - 2
        pdf.set_fill_color(*BAR_BG)
        pdf.rect(bx, yy + 1.3, bw, 2.6, style="F", round_corners=True, corner_radius=1.3)
        pdf.set_fill_color(*ACCENT)
        pdf.rect(bx, yy + 1.3, max(1.6, bw * len(groups[t]) / top), 2.6, style="F",
                 round_corners=True, corner_radius=1.3)
        pdf.set_xy(x + colw - numw, yy)
        pdf.font(8.2, "B")
        pdf.cell(numw, rh - 1.4, str(len(groups[t])), align="R")
    y += half * rh + 6

    # ---- deadlines box -------------------------------------------------------------------------------------
    MAXD = 8
    pdf.set_xy(L, y)
    lines = deadlines[:MAXD]
    inner = W - 10
    heights = []
    for r in lines:
        pdf.font(8.4)
        heights.append(pdf.multi_cell(inner - 30, 4.3, pdf.safe(r.get("title", "")), dry_run=True, output="HEIGHT",
                                      align="L"))
    box_h = 12 + (sum(heights) + 1.6 * len(lines) if lines else 5) + (5 if len(deadlines) > MAXD else 0)
    if y + box_h > pdf.h - pdf.b_margin:
        pdf.add_page()
        y = pdf.get_y()
    pdf.set_fill_color(*ACCENT_SOFT)
    pdf.rect(L, y, W, box_h, style="F", round_corners=True, corner_radius=2)
    pdf.set_fill_color(*ACCENT)
    pdf.rect(L, y, 1.2, box_h, style="F")
    pdf.set_xy(L + 5, y + 3.5)
    pdf.font(8, semibold=True, color=ACCENT)
    pdf.cell(0, 4.5, "DEADLINES & EXTENSIONS")
    yy = y + 10
    if not lines:
        pdf.set_xy(L + 5, yy)
        pdf.font(8.4, color=MUTED)
        pdf.cell(0, 4.3, "No deadline or extension notices in this period.")
    for r, h in zip(lines, heights):
        pdf.set_xy(L + 5, yy)
        pdf.font(7.8, color=MUTED)
        pdf.cell(30, 4.3, pdf.safe(f"{fmt_date(r.get('date'))} · {r.get('regulator', '')}"))
        pdf.set_xy(L + 35, yy)
        pdf.font(8.4)
        pdf.multi_cell(inner - 30, 4.3, pdf.safe(r.get("title", "")), link=r.get("url") or "", align="L")
        yy += h + 1.6
    if len(deadlines) > MAXD:
        pdf.set_xy(L + 5, yy)
        pdf.font(7.8, color=MUTED)
        pdf.cell(0, 4.3, f"+ {len(deadlines) - MAXD} more in the sections below")
    y += box_h + 9

    # ---- body: sections by topic ---------------------------------------------------------------------------
    TW = W  # title width

    def entry_height(r: dict) -> float:
        pdf.font(9.6, semibold=True)
        return 5.2 + pdf.multi_cell(TW, 4.7, pdf.safe(r.get("title", "")), dry_run=True, output="HEIGHT",
                                   align="L") + 4.2

    limit = lambda: pdf.h - pdf.b_margin  # noqa: E731
    for t in order:
        rs = groups[t]
        need = 12 + entry_height(rs[0])
        if y + need > limit():
            pdf.add_page()
            y = pdf.get_y() + 2
        y = section_title(t, y, f"{len(rs)} update{'s' if len(rs) != 1 else ''}")
        pdf.start_section(pdf.safe(t))
        for n, r in enumerate(rs):
            h = entry_height(r)
            if y + h > limit():
                pdf.add_page()
                y = pdf.get_y() + 2
            # meta line: date · regulator pill · doc type · ref no  ...  links
            x = L
            pdf.set_xy(x, y)
            pdf.font(7.8, color=MUTED)
            d = fmt_date(r.get("date"))
            pdf.cell(pdf.get_string_width(d) + 2.5, 4.2, d)
            x = pdf.get_x()
            x += pdf.pill(r.get("regulator") or "-", x, y) + 2.5
            dt, ref = (r.get("doc_type") or "").strip(), (r.get("ref_no") or "").strip()
            if dt and ref.lower().startswith(dt.lower()):
                dt = ""  # "Notification · Notification No. 5/2026" -> just the ref no
            meta = " · ".join(p for p in (dt, ref) if p)
            if DEADLINE_TAG in (r.get("tags") or []):
                meta = (meta + " · " if meta else "") + "Deadline / extension"
            links = [("PDF", r.get("pdf_url"))] if r.get("pdf_url") else []
            pdf.font(7.2, semibold=True)
            linkw = sum(pdf.get_string_width(a) + 4 for a, _ in links)
            pdf.font(7.8, color=MUTED)
            meta = pdf.safe(meta)
            room = L + W - linkw - x - 1
            while meta and pdf.get_string_width(meta) > room:
                meta = meta[:-2].rstrip() + "…" if len(meta) > 2 else ""
                meta = meta.replace("……", "…")
            pdf.set_xy(x, y)
            pdf.cell(room, 4.2, meta)
            lx = L + W - linkw
            for a, u in links:
                pdf.font(7.2, semibold=True, color=ACCENT)
                w = pdf.get_string_width(a) + 4
                pdf.set_draw_color(*ACCENT)
                pdf.set_line_width(0.25)
                pdf.rect(lx, y + 0.1, w, 4, style="D", round_corners=True, corner_radius=1)
                pdf.set_xy(lx, y + 0.1)
                pdf.cell(w, 4, a, align="C", link=u)
                lx += w
            # title (clickable)
            pdf.set_xy(L, y + 5.2)
            pdf.font(9.6, semibold=True)
            pdf.multi_cell(TW, 4.7, pdf.safe(r.get("title", "")), link=r.get("url") or "", align="L",
                           new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            y = pdf.get_y() + 2.2
            if n < len(rs) - 1:
                pdf.set_draw_color(*RULE)
                pdf.set_line_width(0.15)
                pdf.line(L, y, L + W, y)
            y += 2.0
        y += 6

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(out_path))
    return out_path
