# RegWatch

Free, open-source Windows tool that pulls the latest Indian regulatory updates relevant to chartered accountants into one Excel tracker — and optionally downloads the PDFs.

No account, no API keys, no subscription. Double-click `RegWatch.exe`, pick a period, click **Fetch updates**.

## What it covers

| Group | Sources |
|---|---|
| ICAI & Audit | ICAI announcements, ICAI exposure drafts open for comment, NFRA circulars / orders / consultation papers / inspection reports / press briefs |
| Direct Tax | CBDT notifications, circulars and updates (from the e-filing portal's Latest News) |
| GST & Customs | CBIC GST + Customs notifications, circulars, instructions; GSTN news & advisories |
| SEBI | Circulars, master circulars, regulations, gazette notifications, press releases |
| RBI | Notifications (incl. FEMA), master directions / circulars, press releases (unticked by default — high volume) |
| Labour | Ministry of Labour (Labour Code rules, notifications, press releases), EPFO, ESIC |

**Not covered automatically** — check manually: MCA (mca.gov.in blocks automated access), the full CBDT list on incometaxindia.gov.in (same), MeitY / DPDP Rules (no public listing feed). Contributions with an openly accessible source for these are welcome.

## Output

All in the output folder (default `Documents\RegWatch`):

- `RegWatch_Tracker.xlsx`
  - **Updates** — every item fetched so far, newest first. Yellow rows marked **NEW** were first seen in the latest run. Each row links to the official page and PDF.
  - **Status / Owner / Notes** columns are yours. They are kept every time the tracker refreshes.
  - **Last run** — per-source status, with a link to check any source that failed.
- `PDFs\<Regulator>\<YYYY-MM>\...pdf` — only for the regulators you ticked "PDF" for.
- `tag_rules.json` — keyword rules for the **Topics** column (Audit & Assurance, GST, Labour & Payroll, Deadline / Extension, Students / Exams, ...). Edit to tune.
- `history.db` — local history used to work out what's new.

## Scheduled runs

```
RegWatch.exe --cli --days 7                  # uses the sources/PDF choices last saved in the app
RegWatch.exe --cli --since 01-04-2026 --pdf SEBI,RBI
RegWatch.exe --list                           # list source ids
```

Add the first line to Windows Task Scheduler (e.g. every Monday 9am) to keep the tracker current.

## Caveats

- Titles, dates and links are as published by each regulator. Always rely on the official document.
- Items are filtered by the date shown on the regulator's site. A document posted long after its issue date can fall outside a short window, so run with 30+ days now and then.
- Government sites change their layouts. If a source shows ERROR on the *Last run* sheet, its adapter needs an update (see `regwatch/sources/`).

## Build from source

```powershell
python -m venv .venv; .venv\Scripts\activate
./build.ps1                       # -> dist\RegWatch.exe
python -m regwatch                 # run the app from source
python -m regwatch.probe sebi --days 30   # live-test one adapter
```

Pushing a tag `v*` to GitHub builds the exe and attaches it to a Release (`.github/workflows/release.yml`).

To add a source: subclass `Source` in a module under `regwatch/sources/` (see `base.py`), list it in that module's `SOURCES`, and add the module name to `MODULES` in `sources/__init__.py`.

## Licence

MIT
