"""Excel tracker, laid out like the app's register.

Sheets: 'Latest fetch' (what the last fetch found), 'All updates' (history), 'Events & notices' (events,
courses, student and admin notices kept out of the main sheets), 'Sources' (per-source status) and 'About'.
The team's own columns (Status / Owner / Notes) are read back from the previous file by item UID and kept."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.formatting.rule import CellIsRule, FormulaRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from .dedupe import merge_duplicates
from .palette import FILE_TABS

RUN_SHEET = "Latest fetch"
HISTORY_SHEET = "All updates"
NOISE_SHEET = "Events & notices"
LEGACY_SHEETS = ("Updates", "This run")  # names used by earlier versions, still read for notes
USER_COLS = ["Status", "Owner", "Notes"]
STATUS_CHOICES = ["To review", "Relevant - action needed", "Reviewed / noted", "Not relevant"]
NOISE_TAGS = ("Students / Exams", "Internal / Admin", "Enforcement news", "Events & CPE")
KEY_TAG = "Key change"
FLAG_TAGS = ("Deadline / Extension", "Posted late", KEY_TAG)

COLUMNS = [  # (header, width)
    ("New", 7), ("Date", 11), ("Regulator", 10), ("Type", 15), ("Reference", 26), ("Title", 62),
    ("Topic", 26), ("Key change", 11), ("PDF", 6), ("Status", 21), ("Owner", 12), ("Notes", 38),
    ("Also at", 13), ("UID", 18),
]
COL = {h: i for i, (h, _) in enumerate(COLUMNS, 1)}
HEADER_ROW = 5  # rows 1-3: sheet heading and context

# palette: the app's ledger ink, green ink and stamp violet
INK, INK2, INK3 = "1D2A33", "56646C", "8A969B"
RULE, PAPER = "DDE1DA", "F5F6F2"
GREEN, VIOLET, AMBER, DANGER = "1E6B52", "5B3E96", "8A5A12", "A33A2C"
SANS, SERIF = "Segoe UI", "Sitka Text"

THIN = Side(style="thin", color=RULE)
ROW_BORDER = Border(bottom=THIN)
HEAD_FILL = PatternFill("solid", fgColor=INK)
HEAD_FONT = Font(name="Segoe UI Semibold", size=10, color="FFFFFF")
BODY = Font(name=SANS, size=10, color=INK)
MUTED = Font(name=SANS, size=9.5, color=INK2)
TITLE_LINK = Font(name=SERIF, size=10.5, color=INK)
LINK = Font(name=SANS, size=10, color=GREEN, underline="single")
WRAP_TOP = Alignment(wrap_text=True, vertical="top")
TOP = Alignment(vertical="top")


def _read_user_columns(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    try:
        wb = load_workbook(path, read_only=True)
    except Exception:  # noqa: BLE001 - corrupt/locked file: start fresh rather than fail
        return {}
    out: dict[str, dict[str, str]] = {}
    # older sheets first, then history, then the latest-fetch sheet on top: an edit there wins
    for name in (*LEGACY_SHEETS, NOISE_SHEET, HISTORY_SHEET, RUN_SHEET):
        if name not in wb.sheetnames:
            continue
        rows = wb[name].iter_rows(values_only=True)
        header = None
        for _ in range(12):  # the header row is a few rows down below the sheet heading
            r = next(rows, None)
            if r is None:
                break
            cells = [str(h) if h is not None else "" for h in r]
            if "UID" in cells:
                header = cells
                break
        if not header:
            continue
        iu = header.index("UID")
        idx = {c: header.index(c) for c in USER_COLS if c in header}
        for r in rows:
            if not r or iu >= len(r) or not r[iu]:
                continue
            vals = {c: r[i] for c, i in idx.items() if i < len(r) and r[i] not in (None, "")}
            if vals:
                out.setdefault(str(r[iu]), {}).update(vals)
    wb.close()
    return out


def write_tracker(path: Path, rows: list[dict], new_uids: set[str], run_log: list[dict], sources_by_id: dict,
                  run_uids: set[str] | None = None, user_cols: dict | None = None) -> Path:
    """Write the workbook; if the file is open in Excel, write a timestamped copy instead. Returns the path."""
    keep = user_cols if user_cols is not None else _read_user_columns(path)  # usercols.merged() adds in-app edits
    rows = merge_duplicates(rows)  # one row per document; the primary keeps its UID
    keep = {r["uid"]: _merged_user_values(r, keep) for r in rows if any(u in keep for u in _members(r))}

    def noise(r):
        return bool(set(r.get("tags") or []) & set(NOISE_TAGS))

    run_rows = [r for r in rows if run_uids is None or any(u in run_uids for u in _members(r))]
    when = datetime.now()
    wb = Workbook()
    n_new = sum(1 for r in run_rows if not noise(r) and _is_new(r, new_uids))
    _register(wb.active, RUN_SHEET, [r for r in run_rows if not noise(r)], new_uids, keep, sources_by_id,
              heading="Latest fetch",
              context=f"Fetched {when:%d %b %Y, %H:%M}.  {_count(sum(1 for r in run_rows if not noise(r)), 'update')}, "
                      f"{n_new} new.", tab=GREEN)
    _register(wb.create_sheet(), HISTORY_SHEET, [r for r in rows if not noise(r)], new_uids, keep, sources_by_id,
              heading="All updates", context=f"{_count(sum(1 for r in rows if not noise(r)), 'update')} fetched so far.",
              tab=INK)
    _register(wb.create_sheet(), NOISE_SHEET, [r for r in rows if noise(r)], new_uids, keep, sources_by_id,
              heading="Events & notices",
              context="Events, courses, student and internal admin notices, kept apart from the main register.",
              tab=INK3)
    _sources_sheet(wb.create_sheet("Sources"), run_log, when)
    _about(wb.create_sheet("About"))
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        wb.save(path)
        return path
    except PermissionError:
        alt = path.with_name(f"{path.stem} {datetime.now():%Y-%m-%d %H%M}{path.suffix}")
        wb.save(alt)
        return alt


def _count(n: int, word: str) -> str:
    return f"{n:,} {word}{'s' if n != 1 else ''}"


def _members(r: dict) -> list[str]:
    return r.get("member_uids") or [r["uid"]]


def _is_new(r: dict, new_uids: set[str]) -> bool:
    """A merged document is new only if every source reporting it is new (else it was seen before)."""
    return all(u in new_uids for u in _members(r))


def _merged_user_values(r: dict, keep: dict) -> dict:
    """Status/Owner from the first member that has one (primary first); distinct Notes are joined."""
    out, notes = {}, []
    for u in _members(r):
        vals = keep.get(u, {})
        for c in ("Status", "Owner"):
            if c in vals and (c not in out or out[c] == STATUS_CHOICES[0]):
                out[c] = vals[c]
        n = vals.get("Notes")
        if n not in (None, "") and str(n) not in notes:
            notes.append(str(n))
    if notes:
        out["Notes"] = " | ".join(notes)
    return out


def _source_label(source_id: str, sources_by_id: dict) -> str:
    if source_id.startswith("egazette_"):
        return "e-Gazette"
    if source_id.startswith("pib_"):
        return "PIB"
    src = sources_by_id.get(source_id)
    return src.regulator if src else source_id


def _topic(tags: list[str]) -> str:
    pro = [t for t in tags if t not in FLAG_TAGS and t not in NOISE_TAGS]
    return ", ".join(pro[:2]) if pro else ", ".join(t for t in tags if t in NOISE_TAGS)


def _lines(text, width: float, per_char: float) -> int:
    """Rough number of wrapped lines for `text` in a column `width` wide (Excel width units)."""
    import math
    if not text:
        return 1
    cap = max(4, int(width * per_char))
    return sum(max(1, math.ceil(len(part) / cap)) for part in str(text).splitlines() or [""])


def _row_height(values: dict) -> float:
    """Size a row to its tallest wrapped cell, so long references and notes are never clipped."""
    widths = dict(COLUMNS)
    # calibrated against Excel's own rendering: Sitka (titles) is taller per line than Segoe UI
    title = _lines(values["Title"], widths["Title"], 0.95) * 17.5
    other = max(
        _lines(values["Reference"], widths["Reference"], 1.0),  # long unbroken refs wrap mid-token
        _lines(values["Topic"], widths["Topic"], 1.15),
        _lines(values["Type"], widths["Type"], 1.15),
        _lines(values["Notes"], widths["Notes"], 1.1),
        _lines(values["Also at"], widths["Also at"], 1.15),
    ) * 13.2
    return round(max(title, other) + 6, 1)


def _heading(ws, heading: str, context: str, ncols: int):
    ws.sheet_view.showGridLines = False
    ws.row_dimensions[1].height = 30
    c = ws.cell(1, 1, heading)
    c.font = Font(name=SERIF, size=18, bold=True, color=INK)
    c.alignment = Alignment(vertical="bottom")
    c = ws.cell(2, 1, context)
    c.font = Font(name=SANS, size=10, color=INK2)
    c = ws.cell(3, 1, "Status, Owner and Notes are yours and are kept every time the tracker refreshes. "
                      "Click a title to open it on the regulator's site.")
    c.font = Font(name=SANS, size=9, color=INK3)
    for col in range(1, ncols + 1):
        ws.cell(4, col).border = Border(bottom=Side(style="medium", color=INK))


def _register(ws, title, rows, new_uids, keep, sources_by_id, *, heading: str, context: str, tab: str):
    ws.title = title
    ws.sheet_properties.tabColor = tab
    n_cols = len(COLUMNS)
    _heading(ws, heading, context, n_cols - 1)
    for i, (h, w) in enumerate(COLUMNS, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
        c = ws.cell(HEADER_ROW, i, h)
        c.fill, c.font = HEAD_FILL, HEAD_FONT
        c.alignment = Alignment(vertical="center", horizontal="left", indent=0)
    ws.row_dimensions[HEADER_ROW].height = 22
    ws.freeze_panes = ws.cell(HEADER_ROW + 1, COL["Title"])

    ordered = sorted(rows, key=lambda r: (not _is_new(r, new_uids),
                                          -(r["date"].toordinal() if r["date"] else 0)))
    for n, r in enumerate(ordered, HEADER_ROW + 1):
        user = keep.get(r["uid"], {})
        is_new = _is_new(r, new_uids)
        tags = r.get("tags") or []
        also = []
        for a in r.get("also") or []:
            lab = _source_label(a["source_id"], sources_by_id)
            if lab not in also:
                also.append(lab)
        marker = "New" if is_new else ("Late" if "Posted late" in tags else "")
        values = {
            "New": marker, "Date": r["date"], "Regulator": r["regulator"], "Type": r["doc_type"],
            "Reference": r["ref_no"], "Title": r["title"], "Topic": _topic(tags),
            "Key change": "Key change" if KEY_TAG in tags else ("Deadline" if "Deadline / Extension" in tags else ""),
            "PDF": "", "Status": user.get("Status", "To review" if is_new else ""), "Owner": user.get("Owner", ""),
            "Notes": user.get("Notes", ""), "Also at": ", ".join(also), "UID": r["uid"],
        }
        for h, v in values.items():
            c = ws.cell(n, COL[h], v)
            c.font, c.alignment, c.border = BODY, TOP, ROW_BORDER
        # column-specific styling
        ws.cell(n, COL["Date"]).number_format = "DD-MMM-YY"
        tab_hex = FILE_TABS.get(r["regulator"], ("#" + INK2,))[0].lstrip("#")
        ws.cell(n, COL["Regulator"]).font = Font(name="Segoe UI Semibold", size=10, color=tab_hex)
        for h in ("Type", "Reference", "Topic", "Also at"):
            ws.cell(n, COL[h]).font = MUTED
            ws.cell(n, COL[h]).alignment = WRAP_TOP
        for h in ("Notes", "Owner"):
            ws.cell(n, COL[h]).alignment = WRAP_TOP
        if marker:
            ws.cell(n, COL["New"]).font = Font(name="Segoe UI Semibold", size=9.5,
                                               color=VIOLET if marker == "New" else AMBER)
        kc = ws.cell(n, COL["Key change"])
        if kc.value:
            kc.font = Font(name="Segoe UI Semibold", size=9.5, color=GREEN if kc.value == "Key change" else AMBER)
        t = ws.cell(n, COL["Title"])
        t.font, t.alignment = TITLE_LINK, WRAP_TOP
        if r["url"]:
            t.hyperlink = r["url"]
        p = ws.cell(n, COL["PDF"])
        if r.get("local_pdf"):
            p.value, p.hyperlink, p.font = "Open", Path(r["local_pdf"]).as_uri(), LINK
        elif r["pdf_url"]:
            p.value, p.hyperlink, p.font = "PDF", r["pdf_url"], LINK
        if also:
            link = next((a["pdf_url"] or a["url"] for a in r.get("also") or [] if a["pdf_url"] or a["url"]), "")
            if link:
                ws.cell(n, COL["Also at"]).hyperlink = link
        ws.row_dimensions[n].height = _row_height(values)

    last = max(ws.max_row, HEADER_ROW + 1)
    span = f"A{HEADER_ROW}:{get_column_letter(n_cols)}{last}"
    ws.auto_filter.ref = span
    # Status: dropdown + colour like the app's stamps
    st = get_column_letter(COL["Status"])
    rng = f"{st}{HEADER_ROW + 1}:{st}{max(last, 2000)}"
    dv = DataValidation(type="list", formula1='"' + ",".join(STATUS_CHOICES) + '"', allow_blank=True)
    ws.add_data_validation(dv)
    dv.add(rng)
    styles = {
        "Relevant - action needed": (PatternFill("solid", fgColor="F6E3DF"), Font(color=DANGER, bold=True)),
        "Reviewed / noted": (PatternFill("solid", fgColor="E3EEE8"), Font(color=GREEN)),
        "Not relevant": (None, Font(color=INK3)),
        "To review": (None, Font(color=VIOLET)),
    }
    for val, (fill, font) in styles.items():
        ws.conditional_formatting.add(rng, CellIsRule(operator="equal", formula=[f'"{val}"'], fill=fill, font=font))
    # dim rows marked "Not relevant"
    first = HEADER_ROW + 1
    ws.conditional_formatting.add(
        f"A{first}:{get_column_letter(n_cols - 1)}{max(last, 2000)}",
        FormulaRule(formula=[f'${st}{first}="Not relevant"'], font=Font(color=INK3)))
    ws.column_dimensions[get_column_letter(COL["UID"])].hidden = True
    # printing
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth, ws.page_setup.fitToHeight = 1, 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.print_title_rows = f"{HEADER_ROW}:{HEADER_ROW}"
    if len(rows) == 0:
        c = ws.cell(HEADER_ROW + 1, COL["Title"], "Nothing here yet.")
        c.font = Font(name=SANS, size=10, color=INK3, italic=True)


def _sources_sheet(ws, run_log, when: datetime):
    ws.sheet_properties.tabColor = INK3
    cols = [("Group", 22), ("Source", 46), ("Status", 10), ("In period", 10), ("New", 8), ("Message", 50),
            ("Website", 10)]
    _heading(ws, "Sources", f"Checked {when:%d %b %Y, %H:%M}.  "
                            f"{sum(1 for e in run_log if e['status'] == 'OK')} of {len(run_log)} read successfully.",
             len(cols))
    ws.cell(3, 1).value = "If a source shows Error, its website may have changed. Use the link to check it by hand."
    for i, (h, w) in enumerate(cols, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
        c = ws.cell(HEADER_ROW, i, h)
        c.fill, c.font = HEAD_FILL, HEAD_FONT
    ws.row_dimensions[HEADER_ROW].height = 22
    ws.freeze_panes = ws.cell(HEADER_ROW + 1, 1)
    for n, e in enumerate(sorted(run_log, key=lambda e: (e["status"] == "OK", e["group"], e["name"])), HEADER_ROW + 1):
        vals = [e["group"], e["name"], "OK" if e["status"] == "OK" else "Error", e["count"], e["new"], e["message"],
                "Open" if e["homepage"] else ""]
        for i, v in enumerate(vals, 1):
            c = ws.cell(n, i, v)
            c.font, c.alignment, c.border = BODY, TOP, ROW_BORDER
        ws.cell(n, 1).font = MUTED
        ws.cell(n, 6).font, ws.cell(n, 6).alignment = Font(name=SANS, size=9.5, color=DANGER), WRAP_TOP
        ok = e["status"] == "OK"
        ws.cell(n, 3).font = Font(name="Segoe UI Semibold", size=10, color=GREEN if ok else DANGER)
        if e["homepage"]:
            ws.cell(n, 7).hyperlink, ws.cell(n, 7).font = e["homepage"], LINK


def _about(ws):
    ws.sheet_properties.tabColor = INK3
    ws.sheet_view.showGridLines = False
    ws.column_dimensions["A"].width = 110
    c = ws.cell(1, 1, "About this tracker")
    c.font = Font(name=SERIF, size=18, bold=True, color=INK)
    lines = [
        ("Latest fetch", "only what the most recent fetch found, for the sources and period you picked."),
        ("All updates", "everything fetched so far."),
        ("Events & notices", "webinars, courses, student and internal admin notices, kept out of the main sheets."),
        ("New", "first seen in the latest fetch.  Late: published after its date had already been checked."),
        ("Key change", "new or amended rules, regulations, master directions, frameworks and standards, or "
                       "requirements taking effect.  Deadline: a due date or timeline change."),
        ("Status, Owner, Notes", "yours. They are kept every time the tracker refreshes, whether you edit them "
                                 "here or in the app."),
        ("Also at", "the same document published by another source (e.g. e-Gazette, PIB); click for the first one."),
        ("Topics", "keyword-based. Edit tag_rules.json in the output folder to tune them."),
        ("Accuracy", "titles and dates are as published on the regulator's website. Rely on the official document "
                     "before acting."),
    ]
    for n, (k, v) in enumerate(lines, 3):
        c = ws.cell(n, 1)
        from openpyxl.cell.rich_text import CellRichText, TextBlock
        from openpyxl.cell.text import InlineFont
        c.value = CellRichText(TextBlock(InlineFont(rFont=SANS, sz=10.5, b=True, color=INK), k + "   "),
                               TextBlock(InlineFont(rFont=SANS, sz=10.5, color=INK2), v))
        c.alignment = Alignment(wrap_text=True, vertical="top")
