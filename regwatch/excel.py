"""Excel tracker. Rebuilt every run from history, but the user's own columns
(Status / Owner / Notes) are read back from the previous file and preserved by item UID."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

USER_COLS = ["Status", "Owner", "Notes"]
STATUS_CHOICES = ["To review", "Relevant - action needed", "Reviewed / noted", "Not relevant"]
COLUMNS = [  # (header, width)
    ("New", 6), ("Date", 11), ("Regulator", 10), ("Type", 15), ("Ref No", 22), ("Title", 80),
    ("Topics", 28), ("PDF", 9), ("Status", 20), ("Owner", 12), ("Notes", 36), ("First seen", 16),
    ("Source", 26), ("UID", 18),
]
HEAD_FILL = PatternFill("solid", fgColor="1F3864")
NEW_FILL = PatternFill("solid", fgColor="FFF2CC")
LINK = Font(color="0563C1", underline="single")


def _read_user_columns(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    try:
        wb = load_workbook(path, read_only=True)
    except Exception:  # noqa: BLE001 - corrupt/locked file: start fresh rather than fail
        return {}
    if "Updates" not in wb.sheetnames:
        return {}
    ws = wb["Updates"]
    rows = ws.iter_rows(values_only=True)
    header = [str(h) if h is not None else "" for h in next(rows, [])]
    if "UID" not in header:
        return {}
    iu = header.index("UID")
    idx = {c: header.index(c) for c in USER_COLS if c in header}
    out = {}
    for r in rows:
        if not r or iu >= len(r) or not r[iu]:
            continue
        vals = {c: r[i] for c, i in idx.items() if i < len(r) and r[i] not in (None, "")}
        if vals:
            out[str(r[iu])] = vals
    wb.close()
    return out


def write_tracker(path: Path, rows: list[dict], new_uids: set[str], run_log: list[dict], sources_by_id: dict) -> Path:
    """Write the workbook; if the file is open in Excel, write a timestamped copy instead. Returns the path written."""
    keep = _read_user_columns(path)
    wb = Workbook()
    ws = wb.active
    ws.title = "Updates"
    ws.append([h for h, _ in COLUMNS])
    for i, (_, w) in enumerate(COLUMNS, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
        c = ws.cell(row=1, column=i)
        c.fill, c.font, c.alignment = HEAD_FILL, Font(color="FFFFFF", bold=True), Alignment(vertical="center")
    ws.freeze_panes = "C2"
    rows = sorted(rows, key=lambda r: (r["uid"] not in new_uids, -(r["date"].toordinal() if r["date"] else 0)))
    for r in rows:
        user = keep.get(r["uid"], {})
        src = sources_by_id.get(r["source_id"])
        is_new = r["uid"] in new_uids
        ws.append([
            "NEW" if is_new else "", r["date"], r["regulator"], r["doc_type"], r["ref_no"], r["title"],
            ", ".join(r["tags"]), "", user.get("Status", "To review" if is_new else ""), user.get("Owner", ""),
            user.get("Notes", ""), r["first_seen"], src.name if src else r["source_id"], r["uid"],
        ])
        n = ws.max_row
        ws.cell(n, 2).number_format = "DD-MMM-YYYY"
        ws.cell(n, 12).number_format = "DD-MMM-YY HH:MM"
        t = ws.cell(n, 6)
        if r["url"]:
            t.hyperlink, t.font = r["url"], LINK
        p = ws.cell(n, 8)
        if r.get("local_pdf"):
            p.value, p.hyperlink, p.font = "Open", Path(r["local_pdf"]).as_uri(), LINK
        elif r["pdf_url"]:
            p.value, p.hyperlink, p.font = "Online", r["pdf_url"], LINK
        if is_new:
            for c in range(1, len(COLUMNS) + 1):
                ws.cell(n, c).fill = NEW_FILL
    last = max(ws.max_row, 2)
    ws.auto_filter.ref = f"A1:{get_column_letter(len(COLUMNS))}{last}"
    dv = DataValidation(type="list", formula1='"' + ",".join(STATUS_CHOICES) + '"', allow_blank=True)
    ws.add_data_validation(dv)
    dv.add(f"I2:I{max(last, 5000)}")
    ws.column_dimensions["N"].hidden = True

    lg = wb.create_sheet("Last run")
    lg.append(["Run at", datetime.now().strftime("%d-%b-%Y %H:%M")])
    lg.append([])
    lg.append(["Group", "Source", "Status", "Items in period", "New", "Message", "Check manually at"])
    for c in lg[3]:
        c.fill, c.font = HEAD_FILL, Font(color="FFFFFF", bold=True)
    for e in run_log:
        lg.append([e["group"], e["name"], e["status"], e["count"], e["new"], e["message"], e["homepage"]])
        cell = lg.cell(lg.max_row, 7)
        if e["homepage"]:
            cell.hyperlink, cell.font = e["homepage"], LINK
        if e["status"] != "OK":
            lg.cell(lg.max_row, 3).font = Font(color="C00000", bold=True)
    for col, w in zip("ABCDEFG", (16, 40, 10, 15, 7, 60, 60)):
        lg.column_dimensions[col].width = w

    about = wb.create_sheet("About")
    for line in [
        "RegWatch - free tracker of Indian regulatory updates (open source).",
        "Updates sheet: every item fetched so far. Yellow rows / 'NEW' = first seen in the latest run.",
        "Status, Owner and Notes are yours: they are carried over each time the tracker is refreshed.",
        "Topics are keyword-based (edit tag_rules.json in the output folder to tune them).",
        "Always rely on the official document - titles/dates are as published on the regulator's site.",
        "If a source shows an error on 'Last run', the site layout may have changed - use the link to check manually.",
    ]:
        about.append([line])
    about.column_dimensions["A"].width = 120

    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        wb.save(path)
        return path
    except PermissionError:
        alt = path.with_name(f"{path.stem} {datetime.now():%Y-%m-%d %H%M}{path.suffix}")
        wb.save(alt)
        return alt
