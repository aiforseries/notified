# RegWatch

Free, open-source Windows app that pulls the latest Indian regulatory updates relevant to chartered accountants into one place: an in-app results browser, an Excel tracker, PDFs, full-text search and a client-ready digest.

No account, no API keys, no subscription. Download `RegWatch.exe` from [Releases](https://github.com/aiforseries/regwatch/releases/latest), double-click, pick a period, click **Fetch updates**.

## What it covers (69 sources)

| Group | Sources |
|---|---|
| ICAI & Audit | ICAI announcements, exposure drafts open for comment; NFRA circulars, orders, consultation papers, inspection reports, press briefs |
| MCA | e-Gazette (Ministry of Corporate Affairs rules & notifications), PIB press releases |
| Direct Tax | CBDT notifications / circulars / updates (e-filing portal), e-Gazette CBDT notifications, PIB CBDT releases |
| GST & Customs | CBIC GST + Customs notifications, circulars, instructions; GSTN advisories; PIB CBIC releases; DGFT notifications, public notices, trade notices, circulars |
| SEBI | Circulars, master circulars, regulations, gazette notifications, press releases |
| RBI | Notifications (incl. FEMA), master directions / circulars, press releases |
| Labour | Ministry of Labour (Labour Code rules, notifications, press releases), EPFO, ESIC, e-Gazette Labour |
| Data Protection | e-Gazette MeitY (DPDP Act / Rules, IT Act), PIB MeitY releases |
| Insolvency & Valuation | IBBI regulations, circulars, notifications, guidelines, discussion papers, press releases |
| IFSC / GIFT City | IFSCA circulars, regulations, notifications, guidelines, press releases |
| Corporate Law | ICSI standards & updates; CCI regulations, press releases, combination approvals |
| Insurance & Pension | IRDAI circulars, regulations, notifications, guidelines; PFRDA circulars, regulations, notifications, guidelines |

High-volume feeds (RBI press releases, PIB Finance, CCI combination approvals) are unticked by default.
mca.gov.in and incometaxindia.gov.in block automated access, so they are reached through the e-Gazette and PIB instead.

## Features

- **Register** – every update as a dated register entry: regulator, type, reference number and title, newest first. Search titles, reference numbers *and the text inside downloaded PDFs*; filter by regulator or topic. Open an entry to read it on the regulator's site or open the PDF, and stamp it *To review / Action / Noted / Not relevant* with an owner and notes. Keyboard: `/` search, `↑ ↓` move, `Enter` open, `o` open on site, `Esc` close.
- **Excel tracker** – `This run` (what you just fetched) and `All updates` (history). Status / Owner / Notes are kept across refreshes, whether you edit them in Excel or in the app.
- **Digest PDF** – one click builds an A4 summary of the period grouped by topic, with a deadlines & extensions box, ready to send to a partner or client.
- **Duplicates merged** – the same document seen via the regulator, e-Gazette and PIB shows as one row ("Also at").
- **Topic tags** – keyword rules (Audit, GST, Labour & Payroll, Deadline / Extension, Posted late, …); student/exam and internal admin notices are tagged so they can be hidden. Edit `tag_rules.json` in the output folder to tune.
- **Posted late** – a document that appears after its date was already checked is still caught and flagged.
- **Runs in the background** – optional daily / weekly checks from the system tray with a Windows notification ("5 new updates: SEBI 3, RBI 2"); can start with Windows.
- **Update check** – tells you when a newer RegWatch is on GitHub (can be turned off in Settings).
- Light / dark theme (follows Windows if you like). The window uses Windows' built-in Edge WebView2, so nothing extra to install on Windows 10/11.

## Command line

```
RegWatch.exe --cli --days 7                    # fetch with the sources/PDF choices saved in the app
RegWatch.exe --cli --since 01-04-2026 --pdf SEBI,RBI --digest digest.pdf
RegWatch.exe --search "related party"          # search history + PDF text
RegWatch.exe --list                            # list source ids
```

## Caveats

- Titles, dates and links are as published by each regulator; always rely on the official document.
- Government sites change layouts. A broken source shows as ERROR on the tracker's *Last run* sheet (with a link to check manually) and never stops the others.
- PDF search needs the PDFs downloaded (tick "Save PDFs" for a regulator). Scanned PDFs and Hindi text aren't searchable.

## Build from source

```powershell
python -m venv .venv; .venv\Scripts\activate
./build.ps1                                # -> dist\RegWatch.exe
python -m regwatch                         # run the app from source
python -m regwatch.probe sebi --days 30    # live-test adapters
python tests/test_dedupe.py
```

Pushing a tag `v*` builds the exe on GitHub Actions and attaches it to a Release.

To add a source: subclass `Source` in a module under `regwatch/sources/` (see `base.py`), list it in the module's `SOURCES`, and add the module name to `MODULES` in `sources/__init__.py`. Only use openly published listings.

## Licence

MIT
