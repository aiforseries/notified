"""Desktop window: pick period, sources and PDF regulators, run, open the tracker."""
from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
from datetime import date, timedelta
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import __version__
from .dates import parse_date
from .runner import TRACKER_NAME, Settings, run
from .sources import all_sources

PERIODS = [("Last 7 days", 7), ("Last 30 days", 30), ("Last 90 days", 90), ("Since 1 April (FY)", -1), ("Custom from", 0)]


def _fy_start(today: date) -> date:
    return date(today.year if today.month >= 4 else today.year - 1, 4, 1)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"RegWatch {__version__} - Indian regulatory updates")
        self.geometry("880x720")
        self.minsize(760, 600)
        self.settings = Settings.load()
        self.sources = all_sources()
        self.q: queue.Queue = queue.Queue()
        self.worker: threading.Thread | None = None
        self.cancel_flag = False
        self.last_tracker: Path | None = None
        self._build()
        self.after(150, self._drain)

    # ---------- layout ----------
    def _build(self):
        pad = dict(padx=10, pady=4)
        style = ttk.Style(self)
        if "vista" in style.theme_names():
            style.theme_use("vista")

        top = ttk.LabelFrame(self, text="1. Period")
        top.pack(fill="x", **pad)
        self.period = tk.IntVar(value=self.settings.days if self.settings.days in (7, 30, 90, -1) else 30)
        for label, val in PERIODS:
            ttk.Radiobutton(top, text=label, variable=self.period, value=val).pack(side="left", padx=6, pady=6)
        self.custom = ttk.Entry(top, width=12)
        self.custom.insert(0, (date.today() - timedelta(days=30)).strftime("%d-%m-%Y"))
        self.custom.pack(side="left")
        ttk.Label(top, text="(dd-mm-yyyy)").pack(side="left", padx=4)

        mid = ttk.LabelFrame(self, text="2. Sources  (tick 'PDF' to also save the documents for that regulator)")
        mid.pack(fill="both", expand=True, **pad)
        canvas = tk.Canvas(mid, highlightthickness=0, height=300)
        sb = ttk.Scrollbar(mid, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas)
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(int(-e.delta / 120), "units"))

        enabled = set(self.settings.enabled) if self.settings.enabled else {s.id for s in self.sources if s.enabled_by_default}
        self.src_vars: dict[str, tk.BooleanVar] = {}
        self.pdf_vars: dict[str, tk.BooleanVar] = {}
        groups: dict[str, list] = {}
        for s in self.sources:
            groups.setdefault(s.group, []).append(s)
        row = 0
        for g, srcs in groups.items():
            ttk.Label(inner, text=g, font=("Segoe UI", 10, "bold")).grid(row=row, column=0, sticky="w", pady=(8, 0))
            gvars = []
            btn = ttk.Button(inner, text="all / none", width=10)
            btn.grid(row=row, column=1, sticky="w", pady=(8, 0))
            row += 1
            for s in srcs:
                v = tk.BooleanVar(value=s.id in enabled)
                self.src_vars[s.id] = v
                gvars.append(v)
                label = s.name + ("" if s.enabled_by_default else "   (off by default)")
                ttk.Checkbutton(inner, text=label, variable=v).grid(row=row, column=0, sticky="w", padx=18)
                if s.regulator not in self.pdf_vars:
                    pv = tk.BooleanVar(value=s.regulator in self.settings.pdf_regulators)
                    self.pdf_vars[s.regulator] = pv
                    ttk.Checkbutton(inner, text=f"PDF ({s.regulator})", variable=pv).grid(row=row, column=1, sticky="w", padx=8)
                row += 1
            btn.configure(command=lambda vs=gvars: [v.set(not all(x.get() for x in vs)) for v in vs])

        out = ttk.LabelFrame(self, text="3. Output folder (Excel tracker, PDFs, history)")
        out.pack(fill="x", **pad)
        self.out_var = tk.StringVar(value=self.settings.output_dir)
        ttk.Entry(out, textvariable=self.out_var).pack(side="left", fill="x", expand=True, padx=6, pady=6)
        ttk.Button(out, text="Browse...", command=self._browse).pack(side="left", padx=6)

        act = ttk.Frame(self)
        act.pack(fill="x", **pad)
        self.run_btn = ttk.Button(act, text="Fetch updates", command=self._start)
        self.run_btn.pack(side="left")
        self.stop_btn = ttk.Button(act, text="Stop", command=self._stop, state="disabled")
        self.stop_btn.pack(side="left", padx=6)
        self.open_btn = ttk.Button(act, text="Open Excel tracker", command=self._open_tracker)
        self.open_btn.pack(side="left", padx=6)
        ttk.Button(act, text="Open folder", command=lambda: self._open(Path(self.out_var.get()))).pack(side="left")
        ttk.Button(act, text="About", command=self._about).pack(side="right")
        self.bar = ttk.Progressbar(self, mode="indeterminate")
        self.bar.pack(fill="x", padx=10)

        self.logbox = tk.Text(self, height=10, state="disabled", font=("Consolas", 9), wrap="none")
        self.logbox.pack(fill="both", expand=False, padx=10, pady=(4, 10))

    # ---------- actions ----------
    def _since(self) -> date | None:
        p = self.period.get()
        if p == -1:
            return _fy_start(date.today())
        if p == 0:
            d = parse_date(self.custom.get())
            if not d or d > date.today():
                messagebox.showerror("RegWatch", "Enter the custom start date as dd-mm-yyyy (not in the future).")
                return None
            return d
        return date.today() - timedelta(days=p)

    def _start(self):
        since = self._since()
        if not since:
            return
        ids = [i for i, v in self.src_vars.items() if v.get()]
        if not ids:
            messagebox.showinfo("RegWatch", "Tick at least one source.")
            return
        pdf_regs = [r for r, v in self.pdf_vars.items() if v.get()]
        out = Path(self.out_var.get()).expanduser()
        self.settings.days = self.period.get() if self.period.get() else 30
        self.settings.enabled, self.settings.pdf_regulators, self.settings.output_dir = ids, pdf_regs, str(out)
        self.settings.save()
        self.cancel_flag = False
        self.run_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.bar.start(12)
        self._clear_log()

        def work():
            try:
                res = run(since, ids, pdf_regs, out, progress=lambda m: self.q.put(("log", m)),
                          cancel=lambda: self.cancel_flag)
                self.q.put(("done", res))
            except Exception as e:  # noqa: BLE001
                self.q.put(("error", f"{type(e).__name__}: {e}"))

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def _stop(self):
        self.cancel_flag = True
        self._log("Stopping after current requests ...")

    def _drain(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "log":
                    self._log(payload)
                elif kind == "done":
                    self._finish()
                    self.last_tracker = payload.tracker
                    msg = f"{payload.new_count} new update(s); {payload.total_in_period} in the period."
                    if payload.pdfs_saved:
                        msg += f"\n{payload.pdfs_saved} PDF(s) saved."
                    if payload.failures:
                        msg += f"\n\n{len(payload.failures)} source(s) could not be read:\n- " + "\n- ".join(payload.failures)
                        msg += "\n\n(See the 'Last run' sheet for links to check them manually.)"
                    if payload.tracker.name != TRACKER_NAME:
                        msg += f"\n\nThe tracker was open in Excel, so a copy was saved as:\n{payload.tracker.name}"
                    if messagebox.askyesno("RegWatch", msg + "\n\nOpen the Excel tracker now?"):
                        self._open(payload.tracker)
                elif kind == "error":
                    self._finish()
                    messagebox.showerror("RegWatch", payload)
        except queue.Empty:
            pass
        self.after(150, self._drain)

    def _finish(self):
        self.bar.stop()
        self.run_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")

    def _browse(self):
        d = filedialog.askdirectory(initialdir=self.out_var.get() or str(Path.home()))
        if d:
            self.out_var.set(d)

    def _open_tracker(self):
        p = self.last_tracker or Path(self.out_var.get()) / TRACKER_NAME
        if p.exists():
            self._open(p)
        else:
            messagebox.showinfo("RegWatch", "No tracker yet - click 'Fetch updates' first.")

    @staticmethod
    def _open(p: Path):
        p.mkdir(parents=True, exist_ok=True) if not p.suffix else None
        os.startfile(str(p))  # noqa: S606 - Windows only, user-chosen path

    def _about(self):
        off = [s.name for s in self.sources if not s.enabled_by_default]
        messagebox.showinfo("About RegWatch", (
            f"RegWatch {__version__} - free and open source.\n\n"
            "Reads the public listing pages of Indian regulators and builds an Excel tracker. "
            "Titles, dates and links are as published by each regulator; always rely on the official document.\n\n"
            "Topic tags are keyword-based - edit tag_rules.json in the output folder to tune them.\n\n"
            "To run on a schedule (e.g. every Monday), use Windows Task Scheduler with:\n"
            "  RegWatch.exe --cli --days 7"
            + ("\n\nUnticked by default (high volume - tick to include):\n- " + "\n- ".join(off) if off else "")
            + "\n\nNot covered automatically (site blocks automated access or has no public listing)"
              " - check these manually:\n"
              "- MCA notifications & circulars: mca.gov.in\n"
              "- Full CBDT notification list: incometaxindia.gov.in (portal 'Latest News' is covered)\n"
              "- MeitY / DPDP Rules: meity.gov.in/data-protection-framework"
        ))

    def _log(self, m: str):
        self.logbox.configure(state="normal")
        self.logbox.insert("end", m + "\n")
        self.logbox.see("end")
        self.logbox.configure(state="disabled")

    def _clear_log(self):
        self.logbox.configure(state="normal")
        self.logbox.delete("1.0", "end")
        self.logbox.configure(state="disabled")


def main():
    App().mainloop()
