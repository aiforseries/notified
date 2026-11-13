"""Excel tracker. Rebuilt every run from history, but the user's own columns
(Status / Owner / Notes) are read back from the previous file and preserved by item UID."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from .dedupe import merge_duplicates

RUN_SHEET = "This run"
HISTORY_SHEET = "All updates"
USER_COLS = ["Status", "Owner", "Notes"]
STATUS_CHOICES = ["To review", "Relevant - action needed", "Reviewed / noted", "Not relevant"]
COLUMNS = [  # (header, width)
    ("New", 6), ("Date", 11), ("Regulator", 10), ("Type", 15), ("Ref No", 22), ("Title", 80),
    ("Topics", 28), ("PDF", 9), ("Status", 20), ("Owner", 12), ("Notes", 36), ("First seen", 16),
    ("Source", 26), ("Also at", 18), ("UID", 18),
]
COL = {h: i for i, (h, _) in enumerate(COLUMNS, 1)}
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
    out: dict[str, dict[str, str]] = {}
    # History sheet first, then "This run" on top: an edit made on the run sheet wins.
    for name in ("Updates", HISTORY_SHEET, RUN_SHEET):  # "Updates" = v0.1 layout
        if name not in wb.sheetnames:
            continue
        rows = wb[name].iter_rows(values_only=True)
        header = [str(h) if h is not None else "" for h in next(rows, [])]
        if "UID" not in header:
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
    """Sheets: 'This run' (only what this run fetched), 'All updates' (full history), 'Last run', 'About'.
    If the file is open in Excel, writes a timestamped copy instead. Returns the path written."""
    keep = user_cols if user_cols is not None else _read_user_columns(path)  # usercols.merged() adds in-app edits
    rows = merge_duplicates(rows)  # one row per document; the primary keeps its UID
    keep = {r["uid"]: _merged_user_values(r, keep) for r in rows if any(u in keep for u in _members(r))}
    wb = Workbook()
    run_rows = [r for r in rows if run_uids is None or any(u in run_uids for u in _members(r))]
    _updates_sheet(wb.active, RUN_SHEET, run_rows, new_uids, keep, sources_by_id)
    _updates_sheet(wb.create_sheet(), HISTORY_SHEET, rows, new_uids, keep, sources_by_id)
    _rest(wb, run_log)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        wb.save(path)
        return path
    except PermissionError:
        alt = path.with_name(f"{path.stem} {datetime.now():%Y-%m-%d %H%M}{path.suffix}")
        wb.save(alt)
        return alt


def _members(r: dict) -> list[str]:
    return r.get("member_uids") or [r["uid"]]


def _merged_user_values(r: dict, keep: dict) -> dict:
    """Status/Owner from the first member that has one (primary first); distinct Notes are joined."""
    out, notes = {}, []
    for u in _members(r):
        vals = keep.get(u, {})
        for c in ("Status", "Owner"):
            # the default "To review" never hides a real decision made on another member
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
        return "eGazette"
    if source_id.startswith("pib_"):
        return "PIB"
    src = sources_by_id.get(source_id)
    return src.name.split(" (")[0] if src else source_id


def _updates_sheet(ws, title, rows, new_uids, keep, sources_by_id):
    ws.title = title
    ws.append([h for h, _ in COLUMNS])
    for i, (_, w) in enumerate(COLUMNS, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
        c = ws.cell(row=1, column=i)
        c.fill, c.font, c.alignment = HEAD_FILL, Font(color="FFFFFF", bold=True), Alignment(vertical="center")
    ws.freeze_panes = "C2"
    # A merged document is "new" only if every source reporting it is new (else it was seen before).
    rows = [(r, all(u in new_uids for u in _members(r))) for r in rows]
    rows.sort(key=lambda x: (not x[1], -(x[0]["date"].toordinal() if x[0]["date"] else 0)))
    for r, is_new in rows:
        user = keep.get(r["uid"], {})
        src = sources_by_id.get(r["source_id"])
        also = r.get("also") or []
        labels = []
        for a in also:
            lab = _source_label(a["source_id"], sources_by_id)
            if lab not in labels:
                labels.append(lab)
        ws.append([
            "NEW" if is_new else "", r["date"], r["regulator"], r["doc_type"], r["ref_no"], r["title"],
            ", ".join(r["tags"]), "", user.get("Status", "To review" if is_new else ""), user.get("Owner", ""),
            user.get("Notes", ""), r["first_seen"], src.name if src else r["source_id"], ", ".join(labels), r["uid"],
        ])
        n = ws.max_row
        ws.cell(n, COL["Date"]).number_format = "DD-MMM-YYYY"
        ws.cell(n, COL["First seen"]).number_format = "DD-MMM-YY HH:MM"
        if also:
            link = next((a["pdf_url"] or a["url"] for a in also if a["pdf_url"] or a["url"]), "")
            if link:
                c = ws.cell(n, COL["Also at"])
                c.hyperlink, c.font = link, LINK
        t = ws.cell(n, COL["Title"])
        if r["url"]:
            t.hyperlink, t.font = r["url"], LINK
        p = ws.cell(n, COL["PDF"])
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
    st = get_column_letter(COL["Status"])
    dv.add(f"{st}2:{st}{max(last, 5000)}")
    ws.column_dimensions[get_column_letter(COL["UID"])].hidden = True


def _rest(wb, run_log):
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
        f"'{RUN_SHEET}': only the sources and period you picked in the latest run.",
        f"'{HISTORY_SHEET}': everything fetched so far, across all runs.",
        "Yellow rows / 'NEW' = first seen in the latest run. 'Posted late' = appeared after its date was already covered.",
        "Status, Owner and Notes are yours: they are carried over each time the tracker is refreshed (edit on either sheet).",
        "The same document reported by several sources (e.g. regulator site, e-Gazette, PIB) is shown once; "
        "'Also at' lists the other sources (click for the first one).",
        "Topics are keyword-based (edit tag_rules.json in the output folder to tune them).",
        "Always rely on the official document - titles/dates are as published on the regulator's site.",
        "If a source shows an error on 'Last run', the site layout may have changed - use the link to check manually.",
    ]:
        about.append([line])
    about.column_dimensions["A"].width = 120
