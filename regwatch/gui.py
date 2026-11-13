"""Desktop window (CustomTkinter): sidebar with period / output / theme, source cards, live progress, result tiles."""
from __future__ import annotations

import calendar
import os
import queue
import re
import subprocess
import threading
import webbrowser
from datetime import date, datetime, timedelta
from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk

from . import __version__
from .paths import resource
from . import background, updater
from .browser import ResultsView
from .runner import TRACKER_NAME, Settings, run
from .sources import all_sources

from .ui_kit import (ACCENT, ACCENT_HOVER, ACCENT_SOFT, AMBER, BG, BORDER, CARD, CARD_ALT, DANGER, MUTED, OK, TEXT,
                     Card, Pill, font)

PRESETS = [("7 days", 7), ("30 days", 30), ("90 days", 90), ("This FY", -1), ("Custom", 0)]


def fy_start(today: date) -> date:
    return date(today.year if today.month >= 4 else today.year - 1, 4, 1)


class StatTile(ctk.CTkFrame):
    def __init__(self, master, label, color=TEXT):
        super().__init__(master, fg_color=CARD_ALT, corner_radius=12, border_width=1, border_color=BORDER)
        self.value = ctk.CTkLabel(self, text="-", font=font(20, "bold"), text_color=color)
        self.value.pack(side="left", padx=(14, 8), pady=8)
        ctk.CTkLabel(self, text=label, font=font(11), text_color=MUTED).pack(side="left", pady=8)


class DatePicker(ctk.CTkToplevel):
    """Month-grid calendar popup. Future dates are disabled."""

    def __init__(self, master, initial: date, on_pick):
        super().__init__(master)
        self.withdraw()
        self.overrideredirect(True)
        self.configure(fg_color=CARD)
        self.on_pick = on_pick
        self.sel = initial
        self.view = initial.replace(day=1)
        self.body = ctk.CTkFrame(self, fg_color=CARD, corner_radius=12, border_width=1, border_color=BORDER)
        self.body.pack(padx=1, pady=1)
        self.bind("<Escape>", lambda e: self.destroy())
        self.bind("<FocusOut>", lambda e: self.after(150, self._maybe_close))
        self._render()

    def _maybe_close(self):
        try:
            if self.focus_get() is None:
                self.destroy()
        except Exception:  # noqa: BLE001 - window already gone
            pass

    def show_below(self, widget):
        self.update_idletasks()
        self.geometry(f"+{widget.winfo_rootx()}+{widget.winfo_rooty() + widget.winfo_height() + 6}")
        self.deiconify()
        self.lift()
        self.focus_force()

    def _shift(self, months: int):
        y, m = self.view.year, self.view.month + months
        y, m = y + (m - 1) // 12, (m - 1) % 12 + 1
        self.view = date(y, m, 1)
        self._render()

    def _render(self):
        for w in self.body.winfo_children():
            w.destroy()
        head = ctk.CTkFrame(self.body, fg_color="transparent")
        head.grid(row=0, column=0, columnspan=7, sticky="ew", padx=8, pady=(8, 4))
        nav = dict(width=30, height=28, fg_color="transparent", hover_color=ACCENT_SOFT, text_color=TEXT, font=font(14, "bold"))
        ctk.CTkButton(head, text="‹", command=lambda: self._shift(-1), **nav).pack(side="left")
        ctk.CTkLabel(head, text=self.view.strftime("%B %Y"), font=font(13, "bold"), text_color=TEXT).pack(side="left", expand=True)
        nxt = ctk.CTkButton(head, text="›", command=lambda: self._shift(1), **nav)
        nxt.pack(side="right")
        if self.view >= date.today().replace(day=1):
            nxt.configure(state="disabled")
        for i, d in enumerate(["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]):
            ctk.CTkLabel(self.body, text=d, width=34, font=font(11), text_color=MUTED).grid(row=1, column=i, padx=1)
        today = date.today()
        for r, week in enumerate(calendar.Calendar().monthdayscalendar(self.view.year, self.view.month), start=2):
            for c, day in enumerate(week):
                if not day:
                    continue
                d = date(self.view.year, self.view.month, day)
                selected, future = d == self.sel, d > today
                b = ctk.CTkButton(
                    self.body, text=str(day), width=34, height=30, corner_radius=8, font=font(12, "bold" if d == today else "normal"),
                    fg_color=ACCENT if selected else "transparent", hover_color=ACCENT_SOFT,
                    text_color=("#FFFFFF", "#0B1220") if selected else (MUTED if future else TEXT),
                    border_width=1 if d == today and not selected else 0, border_color=ACCENT,
                    state="disabled" if future else "normal", command=lambda d=d: self._pick(d))
                b.grid(row=r, column=c, padx=1, pady=1)
        ctk.CTkFrame(self.body, width=1, height=6, fg_color="transparent").grid(row=9, column=0)

    def _pick(self, d: date):
        self.on_pick(d)
        self.destroy()


# ---------- main window ----------
class App(ctk.CTk):
    def __init__(self, start_hidden: bool = False):
        self.settings = Settings.load()
        ctk.set_appearance_mode(getattr(self.settings, "theme", "system") or "system")
        super().__init__(fg_color=BG)
        self.title("RegWatch")
        self._fit_to_screen()
        self._apply_icon()
        self.after(300, self._apply_icon)  # CustomTkinter swaps in its own icon ~200ms after start
        self.sources = all_sources()
        self.q: queue.Queue = queue.Queue()
        self.cancel_flag = False
        self.last_tracker: Path | None = None
        self.custom_from = date.today() - timedelta(days=30)
        self.src_vars: dict[str, ctk.BooleanVar] = {}
        self.pdf_vars: dict[str, ctk.BooleanVar] = {}
        self.rows: list[tuple[ctk.CTkFrame, str, ctk.CTkFrame]] = []  # (row, search text, group card)
        self.total = self.done = 0
        self.running = self.auto_run = False
        self.tray: background.Tray | None = None
        self._release: updater.Release | None = None

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self._sidebar()
        self._main()
        self._refresh_period()
        self._update_count()
        self.after(120, self._drain)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(4000, self._tick)
        if self.settings.check_updates:
            self.after(2500, self._check_update)
        if start_hidden:
            self.withdraw()
            self._ensure_tray()

    def _apply_icon(self):
        try:
            self.iconbitmap(default=str(resource("assets/regwatch.ico")))
        except Exception:  # noqa: BLE001 - icon is cosmetic
            pass

    def _fit_to_screen(self):
        scale = self._get_window_scaling()
        sw, sh = self.winfo_screenwidth() / scale, self.winfo_screenheight() / scale  # logical px
        w, h = min(1120, int(sw * 0.92)), min(800, int(sh * 0.88))
        self.minsize(min(940, w), min(600, h))
        self.geometry(f"{w}x{h}+{max(0, int((sw - w) / 2))}+{max(0, int((sh - h) / 2.6))}")

    # ----- sidebar -----
    def _sidebar(self):
        sb = ctk.CTkFrame(self, fg_color=CARD, corner_radius=0, width=290, border_width=0)
        sb.grid(row=0, column=0, rowspan=2, sticky="nsw")
        sb.grid_propagate(False)
        sb.grid_columnconfigure(0, weight=1)

        brand = ctk.CTkFrame(sb, fg_color="transparent")
        brand.grid(row=0, column=0, sticky="ew", padx=22, pady=(24, 18))
        try:
            from PIL import Image
            img = Image.open(resource("assets/regwatch.png"))
            ctk.CTkLabel(brand, text="", image=ctk.CTkImage(img, img, size=(40, 40))).pack(side="left")
        except Exception:  # noqa: BLE001
            pass
        t = ctk.CTkFrame(brand, fg_color="transparent")
        t.pack(side="left", padx=12)
        ctk.CTkLabel(t, text="RegWatch", font=font(20, "bold"), text_color=TEXT).pack(anchor="w")
        ctk.CTkLabel(t, text="Indian regulatory updates", font=font(11), text_color=MUTED).pack(anchor="w")

        self._section(sb, 1, "PERIOD")
        per = ctk.CTkFrame(sb, fg_color="transparent")
        per.grid(row=2, column=0, sticky="ew", padx=22)
        self.preset = ctk.StringVar(value=next((l for l, v in PRESETS if v == self.settings.days), "30 days"))
        grid = ctk.CTkFrame(per, fg_color="transparent")
        grid.pack(fill="x")
        self.preset_btns = {}
        for i, (label, _) in enumerate(PRESETS):
            b = ctk.CTkButton(grid, text=label, height=34, corner_radius=9, font=font(12),
                              border_width=1, command=lambda l=label: self._set_preset(l))
            b.grid(row=i // 2, column=i % 2, sticky="ew", padx=3, pady=3)
            self.preset_btns[label] = b
        grid.grid_columnconfigure((0, 1), weight=1)

        self.date_btn = ctk.CTkButton(per, text="", height=40, corner_radius=10, anchor="w", font=font(13),
                                      fg_color=CARD_ALT, hover_color=ACCENT_SOFT, text_color=TEXT,
                                      border_width=1, border_color=BORDER, command=self._open_picker)
        self.date_btn.pack(fill="x", pady=(10, 2), padx=3)
        self.period_hint = ctk.CTkLabel(per, text="", font=font(11), text_color=MUTED, anchor="w")
        self.period_hint.pack(fill="x", padx=5)

        self._section(sb, 3, "SAVE TO")
        out = ctk.CTkFrame(sb, fg_color="transparent")
        out.grid(row=4, column=0, sticky="ew", padx=22)
        self.out_var = ctk.StringVar(value=self.settings.output_dir)
        self.out_var.trace_add("write", lambda *_: self._refresh_out())
        self.out_lbl = ctk.CTkButton(out, text="", height=40, corner_radius=10, anchor="w", font=font(12),
                                     fg_color=CARD_ALT, hover_color=ACCENT_SOFT, text_color=TEXT,
                                     border_width=1, border_color=BORDER, command=self._browse)
        self.out_lbl.pack(fill="x", padx=3)
        self._refresh_out()
        ctk.CTkLabel(out, text="Excel tracker, PDFs and history live here", font=font(11),
                     text_color=MUTED, anchor="w").pack(fill="x", padx=5, pady=(2, 0))

        sb.grid_rowconfigure(5, weight=1)
        foot = ctk.CTkFrame(sb, fg_color="transparent")
        foot.grid(row=6, column=0, sticky="ew", padx=22, pady=(0, 10))
        ctk.CTkLabel(foot, text="THEME", font=font(11, "bold"), text_color=MUTED, anchor="w").pack(fill="x", padx=4, pady=(0, 6))
        self.theme = ctk.CTkSegmentedButton(foot, values=["Light", "Dark", "System"], command=self._set_theme,
                                            font=font(11), selected_color=ACCENT, selected_hover_color=ACCENT_HOVER)
        self.theme.set((getattr(self.settings, "theme", "system") or "system").title())
        self.theme.pack(fill="x", padx=3)
        links = ctk.CTkFrame(sb, fg_color="transparent")
        links.grid(row=7, column=0, pady=(0, 14))
        lk = dict(fg_color="transparent", hover_color=ACCENT_SOFT, text_color=MUTED, font=font(11), height=26, width=70)
        ctk.CTkButton(links, text="Settings", command=self._settings_dialog, **lk).pack(side="left")
        ctk.CTkButton(links, text=f"About · v{__version__}", command=self._about, **lk).pack(side="left")

    def _section(self, parent, row, text):
        ctk.CTkLabel(parent, text=text, font=font(11, "bold"), text_color=MUTED, anchor="w").grid(
            row=row, column=0, sticky="ew", padx=26, pady=(16, 6))

    # ----- main panel -----
    def _main(self):
        main = ctk.CTkFrame(self, fg_color="transparent")
        main.grid(row=0, column=1, sticky="nsew", padx=24, pady=(18, 10))
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(2, weight=1)
        self.main = main

        # row 0: update banner (hidden until a newer release is found)
        self.banner = ctk.CTkFrame(main, fg_color=ACCENT_SOFT, corner_radius=12)
        self.banner_lbl = ctk.CTkLabel(self.banner, text="", font=font(12, "bold"), text_color=ACCENT, anchor="w")
        self.banner_lbl.pack(side="left", padx=14, pady=8)
        self.banner_btn = ctk.CTkButton(self.banner, text="Download", width=100, height=30, corner_radius=8,
                                        fg_color=ACCENT, hover_color=ACCENT_HOVER, font=font(12, "bold"),
                                        command=self._download_update)
        self.banner_btn.pack(side="right", padx=8, pady=6)
        ctk.CTkButton(self.banner, text="What's new", width=90, height=30, corner_radius=8, fg_color="transparent",
                      text_color=ACCENT, hover_color=CARD, font=font(12),
                      command=lambda: webbrowser.open(self._release.page) if self._release else None).pack(side="right")

        # row 1: view switcher + view-specific tools
        top = ctk.CTkFrame(main, fg_color="transparent")
        top.grid(row=1, column=0, sticky="ew", pady=(0, 12))
        self.nav = ctk.CTkSegmentedButton(top, values=["Sources", "Results"], height=38, font=font(15, "bold"),
                                          selected_color=ACCENT, selected_hover_color=ACCENT_HOVER,
                                          command=self._show_view)
        self.nav.set("Sources")
        self.nav.pack(side="left")
        self.fetch_tools = ctk.CTkFrame(top, fg_color="transparent")
        self.fetch_tools.pack(side="left", fill="x", expand=True)
        self.count_pill = Pill(self.fetch_tools, "")
        self.count_pill.pack(side="left", padx=12, pady=(2, 0))
        self.search = ctk.CTkEntry(self.fetch_tools, placeholder_text="Filter sources…", width=200, height=34, corner_radius=10,
                                   border_color=BORDER, fg_color=CARD, font=font(12))
        self.search.pack(side="right")
        self.search.bind("<KeyRelease>", lambda e: self._filter())
        ghost = dict(height=34, width=64, corner_radius=10, fg_color="transparent", border_width=1, border_color=BORDER,
                     text_color=TEXT, hover_color=ACCENT_SOFT, font=font(12))
        ctk.CTkButton(self.fetch_tools, text="None", command=lambda: self._set_all(False), **ghost).pack(side="right", padx=(0, 8))
        ctk.CTkButton(self.fetch_tools, text="All", command=lambda: self._set_all(True), **ghost).pack(side="right", padx=(0, 6))

        # row 2: the two views
        scroll = ctk.CTkScrollableFrame(main, fg_color="transparent", scrollbar_button_color=BORDER)
        scroll.grid(row=2, column=0, sticky="nsew")
        scroll.grid_columnconfigure((0, 1), weight=1, uniform="c")
        self.sources_view = scroll
        self.results_view = ResultsView(main, lambda: self.out_var.get(), {s.id: s for s in self.sources})

        defaults = {s.id for s in self.sources if s.enabled_by_default}
        if self.settings.enabled:  # saved choice + any default-on sources added since that choice was saved
            enabled = set(self.settings.enabled) | (defaults - set(self.settings.known_ids or self.settings.enabled))
        else:
            enabled = defaults
        groups: dict[str, list] = {}
        for s in self.sources:
            groups.setdefault(s.group, []).append(s)
        for n, (g, srcs) in enumerate(groups.items()):
            self._group_card(scroll, n, g, srcs, enabled)

        self._action_bar(main)

    def _group_card(self, parent, n, group, srcs, enabled):
        card = Card(parent)
        card.grid(row=n // 2, column=n % 2, sticky="nsew", padx=6, pady=6)
        head = ctk.CTkFrame(card, fg_color="transparent")
        head.pack(fill="x", padx=16, pady=(14, 6))
        ctk.CTkLabel(head, text=group, font=font(15, "bold"), text_color=TEXT).pack(side="left")
        gvars = []
        gswitch = ctk.CTkSwitch(head, text="", width=40, progress_color=ACCENT, switch_width=36, switch_height=18)
        gswitch.pack(side="right")

        for s in srcs:
            v = ctk.BooleanVar(value=s.id in enabled)
            v.trace_add("write", lambda *_: self._update_count())
            self.src_vars[s.id] = v
            gvars.append(v)
            row = ctk.CTkFrame(card, fg_color="transparent")
            row.pack(fill="x", padx=12, pady=1)
            label = re.sub(rf"^{re.escape(s.regulator)}\s*-\s*", "", s.name)
            label = re.sub(r"\s*\(via [^)]*\)", "", label)  # route detail is noise in the list
            if len(label) > 44:
                label = label[:42].rstrip(" ,(") + "…"
            ctk.CTkCheckBox(row, text=label, variable=v, font=font(12), text_color=TEXT, checkbox_width=18,
                            checkbox_height=18, corner_radius=5, border_width=2, fg_color=ACCENT,
                            hover_color=ACCENT_HOVER).pack(side="left", padx=4, pady=3)
            if not s.enabled_by_default:
                Pill(row, "high volume", fg=("#FEF3C7", "#3A2A0A"), color=AMBER).pack(side="right", padx=4)
            Pill(row, s.regulator, fg=CARD_ALT, color=MUTED).pack(side="right", padx=2)
            self.rows.append((row, f"{s.name} {s.regulator} {group}".lower(), card))

        regs = list(dict.fromkeys(s.regulator for s in srcs))
        foot = ctk.CTkFrame(card, fg_color=CARD_ALT, corner_radius=10)
        foot.pack(fill="x", padx=12, pady=(8, 12))
        ctk.CTkLabel(foot, text="Save PDFs", font=font(11, "bold"), text_color=MUTED).pack(side="left", padx=(10, 6), pady=6)
        for r in regs:
            pv = self.pdf_vars.get(r) or ctk.BooleanVar(value=r in self.settings.pdf_regulators)
            self.pdf_vars[r] = pv
            ctk.CTkSwitch(foot, text=r, variable=pv, font=font(11), text_color=TEXT, progress_color=AMBER,
                          switch_width=30, switch_height=15, width=20).pack(side="left", padx=6, pady=6)

        busy = [False]

        def sync_switch(*_):  # group switch reflects "all ticked"
            if not busy[0]:
                gswitch.select() if all(x.get() for x in gvars) else gswitch.deselect()

        def set_group():
            val = bool(gswitch.get())  # read once: setting rows must not flip the switch mid-loop
            busy[0] = True
            for x in gvars:
                x.set(val)
            busy[0] = False
            sync_switch()

        for v in gvars:
            v.trace_add("write", sync_switch)
        sync_switch()
        gswitch.configure(command=set_group)

    def _action_bar(self, main):
        bar = Card(main)
        bar.grid(row=3, column=0, sticky="ew", pady=(12, 0))
        bar.grid_columnconfigure(1, weight=1)
        self.run_btn = ctk.CTkButton(bar, text="Fetch updates", width=170, height=46, corner_radius=12,
                                     font=font(15, "bold"), fg_color=ACCENT, hover_color=ACCENT_HOVER, command=self._start)
        self.run_btn.grid(row=0, column=0, rowspan=2, padx=16, pady=16)
        self.status = ctk.CTkLabel(bar, text="Ready.", font=font(13, "bold"), text_color=TEXT, anchor="w")
        self.status.grid(row=0, column=1, sticky="sew", pady=(16, 2))
        self.progress = ctk.CTkProgressBar(bar, height=8, corner_radius=4, progress_color=ACCENT, fg_color=BORDER)
        self.progress.set(0)
        self.progress.grid(row=1, column=1, sticky="new", pady=(4, 16))
        btns = ctk.CTkFrame(bar, fg_color="transparent")
        btns.grid(row=0, column=2, rowspan=2, padx=16)
        ghost = dict(height=36, corner_radius=10, fg_color="transparent", border_width=1, border_color=BORDER,
                     text_color=TEXT, hover_color=ACCENT_SOFT, font=font(12))
        self.stop_btn = ctk.CTkButton(btns, text="Stop", width=70, command=self._stop, state="disabled", **ghost)
        self.stop_btn.pack(side="left", padx=4)
        ctk.CTkButton(btns, text="Open tracker", width=112, command=self._open_tracker, **ghost).pack(side="left", padx=4)
        self.digest_btn = ctk.CTkButton(btns, text="Digest PDF", width=96, command=self._make_digest, **ghost)
        self.digest_btn.pack(side="left", padx=4)
        ctk.CTkButton(btns, text="Folder", width=70, command=lambda: self._open(Path(self.out_var.get())), **ghost).pack(side="left", padx=4)
        self.log_btn = ctk.CTkButton(btns, text="Log ▾", width=70, command=self._toggle_log, **ghost)
        self.log_btn.pack(side="left", padx=4)

        self.tiles = ctk.CTkFrame(bar, fg_color="transparent")
        self.t_new = StatTile(self.tiles, "new since last run", ACCENT)
        self.t_all = StatTile(self.tiles, "items in period")
        self.t_pdf = StatTile(self.tiles, "PDFs saved", AMBER)
        self.t_fail = StatTile(self.tiles, "sources unreachable", DANGER)
        for i, t in enumerate((self.t_new, self.t_all, self.t_pdf, self.t_fail)):
            t.grid(row=0, column=i, sticky="ew", padx=5)
            self.tiles.grid_columnconfigure(i, weight=1)
        self.fail_lbl = ctk.CTkLabel(bar, text="", font=font(11), text_color=DANGER, anchor="w", justify="left")
        self.logbox = ctk.CTkTextbox(bar, height=150, font=ctk.CTkFont(family="Consolas", size=11),
                                     fg_color=CARD_ALT, border_width=1, border_color=BORDER, corner_radius=10)
        self.logbox.configure(state="disabled")
        self.log_open = False

    # ----- period -----
    def _since(self) -> date:
        p = dict(PRESETS)[self.preset.get()]
        if p == -1:
            return fy_start(date.today())
        if p == 0:
            return self.custom_from
        return date.today() - timedelta(days=p)

    def _set_preset(self, label):
        self.preset.set(label)
        self._refresh_period()
        if label == "Custom":
            self._open_picker()

    def _refresh_period(self):
        for label, b in self.preset_btns.items():
            on = label == self.preset.get()
            b.configure(fg_color=ACCENT if on else "transparent", hover_color=ACCENT_HOVER if on else ACCENT_SOFT,
                        text_color=("#FFFFFF", "#0B1220") if on else TEXT, border_color=ACCENT if on else BORDER)
        s = self._since()
        self.date_btn.configure(text=f"   From   {s:%d %b %Y}   →   today")
        days = (date.today() - s).days
        self.period_hint.configure(text=f"{days} days · click the date to pick a custom start")

    def _open_picker(self):
        DatePicker(self, self._since(), self._picked).show_below(self.date_btn)

    def _picked(self, d: date):
        self.custom_from = d
        self.preset.set("Custom")
        self._refresh_period()

    # ----- misc ui -----
    def _set_theme(self, v):
        ctk.set_appearance_mode(v.lower())
        self.after(50, self.results_view.restyle)
        self.settings.theme = v.lower()  # type: ignore[attr-defined]
        self.settings.save()

    def _refresh_out(self):
        p = self.out_var.get()
        short = p if len(p) < 34 else "…" + p[-32:]
        self.out_lbl.configure(text=f"   {short}")

    def _browse(self):
        d = filedialog.askdirectory(initialdir=self.out_var.get() or str(Path.home()))
        if d:
            self.out_var.set(d)
            self._refresh_out()

    def _filter(self):
        q = self.search.get().strip().lower()
        visible_cards = set()
        for row, text, card in self.rows:
            show = not q or q in text
            if show:
                visible_cards.add(card)
                if not row.winfo_ismapped():
                    row.pack(fill="x", padx=12, pady=1, before=self._first_footer(card))
            else:
                row.pack_forget()

    @staticmethod
    def _first_footer(card):
        return card.winfo_children()[-1]

    def _set_all(self, val: bool):
        for v in self.src_vars.values():
            v.set(val)

    def _update_count(self):
        n = sum(v.get() for v in self.src_vars.values())
        self.count_pill.configure(text=f"{n} of {len(self.src_vars)} selected")

    def _toggle_log(self):
        self.log_open = not self.log_open
        if self.log_open:
            self.logbox.grid(row=4, column=0, columnspan=3, sticky="ew", padx=16, pady=(0, 16))
            self.log_btn.configure(text="Log ▴")
        else:
            self.logbox.grid_forget()
            self.log_btn.configure(text="Log ▾")

    # ----- run -----
    def _start(self, auto: bool = False):
        if self.running:
            return
        ids = [i for i, v in self.src_vars.items() if v.get()]
        if not ids:
            self.status.configure(text="Tick at least one source.", text_color=DANGER)
            return
        pdf_regs = [r for r, v in self.pdf_vars.items() if v.get()]
        out = Path(self.out_var.get()).expanduser()
        if auto:  # scheduled: pick up from the last scheduled check (with overlap), else the chosen period
            last = datetime.fromisoformat(self.settings.last_auto_run) if self.settings.last_auto_run else None
            since = (last.date() - timedelta(days=2)) if last else self._since()
        else:
            since = self._since()
            self.settings.days = dict(PRESETS)[self.preset.get()] or 30
        self.settings.enabled, self.settings.pdf_regulators, self.settings.output_dir = ids, pdf_regs, str(out)
        self.settings.known_ids = list(self.src_vars)
        self.settings.save()
        self.running, self.auto_run = True, auto
        self.cancel_flag = False
        self.total, self.done = len(ids), 0
        self.run_btn.configure(state="disabled", text="Fetching…")
        self.stop_btn.configure(state="normal")
        self.tiles.grid_forget()
        self.fail_lbl.grid_forget()
        self.progress.set(0)
        self.status.configure(text=f"Checking {len(ids)} sources since {since:%d %b %Y}…", text_color=TEXT)
        self.logbox.configure(state="normal")
        self.logbox.delete("1.0", "end")
        self.logbox.configure(state="disabled")

        def work():
            try:
                res = run(since, ids, pdf_regs, out, progress=lambda m: self.q.put(("log", m)), cancel=lambda: self.cancel_flag)
                self.q.put(("done", res))
            except Exception as e:  # noqa: BLE001
                self.q.put(("error", f"{type(e).__name__}: {e}"))

        threading.Thread(target=work, daemon=True).start()

    def _stop(self):
        self.cancel_flag = True
        self.status.configure(text="Stopping after current requests…")

    def _drain(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "log":
                    self._on_log(payload)
                elif kind == "done":
                    self._on_done(payload)
                elif kind == "error":
                    self.running = False
                    self._reset_buttons()
                    self.status.configure(text=f"Failed: {payload}", text_color=DANGER)
                elif kind == "tray":
                    self._tray_action(payload)
                elif kind == "update":
                    self._show_update(payload)
                elif kind == "update_dl":
                    self._update_downloaded(payload)
                elif kind == "digest":
                    self.digest_btn.configure(state="normal", text="Digest PDF")
                    if isinstance(payload, Path):
                        self.status.configure(text=f"Digest saved: {payload.name}", text_color=OK)
                        os.startfile(str(payload))  # noqa: S606 - file we just wrote
                    else:
                        self.status.configure(text=f"Digest failed: {payload}", text_color=DANGER)
        except queue.Empty:
            pass
        self.after(120, self._drain)

    def _on_log(self, m: str):
        self.logbox.configure(state="normal")
        self.logbox.insert("end", m + "\n")
        self.logbox.see("end")
        self.logbox.configure(state="disabled")
        s = m.strip()
        if s.startswith(("OK ", "FAIL ")):
            self.done += 1
            self.progress.set(self.done / max(self.total, 1) * 0.85)
            self.status.configure(text=f"Checked {self.done} of {self.total} sources…")
        elif s.startswith("PDFs:"):
            a, b = re.findall(r"\d+", s)[:2]
            self.progress.set(0.85 + 0.13 * int(a) / max(int(b), 1))
            self.status.configure(text=f"Downloading PDFs {a}/{b}…")
        elif s.startswith("Downloading PDFs"):
            self.status.configure(text="Downloading PDFs…")
        elif s.startswith("Writing Excel"):
            self.progress.set(0.98)
            self.status.configure(text="Writing Excel tracker…")

    def _on_done(self, res):
        self.running = False
        if self.auto_run:
            self.settings.last_auto_run = datetime.now().isoformat(timespec="seconds")
            self.settings.save()
            if self.settings.notify and res.new_count:
                self._ensure_tray()
                if self.tray:
                    self.tray.notify("RegWatch", self._new_summary(res))
        if self.nav.get() == "Results":
            self.results_view.load()
        self._reset_buttons()
        self.last_tracker = res.tracker
        self.progress.set(1)
        msg = "Done." if not self.cancel_flag else "Stopped."
        if res.tracker.name != TRACKER_NAME:
            msg += f"  Tracker was open in Excel, saved a copy: {res.tracker.name}"
        self.status.configure(text=msg, text_color=OK)
        self.t_new.value.configure(text=str(res.new_count))
        self.t_all.value.configure(text=str(res.total_in_period))
        self.t_pdf.value.configure(text=str(res.pdfs_saved))
        self.t_fail.value.configure(text=str(len(res.failures)), text_color=DANGER if res.failures else MUTED)
        self.tiles.grid(row=2, column=0, columnspan=3, sticky="ew", padx=11, pady=(0, 12))
        if res.failures:
            self.fail_lbl.configure(text="Couldn't read: " + ", ".join(res.failures) + "  — links on the 'Last run' sheet.")
            self.fail_lbl.grid(row=3, column=0, columnspan=3, sticky="ew", padx=18, pady=(0, 12))

    def _reset_buttons(self):
        self.run_btn.configure(state="normal", text="Fetch updates")
        self.stop_btn.configure(state="disabled")

    def _open_tracker(self):
        p = self.last_tracker or Path(self.out_var.get()) / TRACKER_NAME
        if p.exists():
            try:  # fold in Status/Notes edited in the Results view since the last run
                from .runner import rewrite_tracker
                p = rewrite_tracker(Path(self.out_var.get())) or p
            except Exception:  # noqa: BLE001 - open the existing file regardless
                pass
            self._open(p)
        else:
            self.status.configure(text="No tracker yet. Click 'Fetch updates' first.", text_color=MUTED)

    @staticmethod
    def _open(p: Path):
        if not p.suffix:
            p.mkdir(parents=True, exist_ok=True)
        os.startfile(str(p))  # noqa: S606 - Windows only, user-chosen path

    # ----- views -----
    def _show_view(self, name: str):
        if name == "Results":
            self.sources_view.grid_forget()
            self.fetch_tools.pack_forget()
            self.results_view.grid(row=2, column=0, sticky="nsew")
            self.results_view.load()
        else:
            self.results_view.grid_forget()
            self.fetch_tools.pack(side="left", fill="x", expand=True)
            self.sources_view.grid(row=2, column=0, sticky="nsew")

    # ----- tray & schedule -----
    def _ensure_tray(self):
        if self.tray is None:
            try:
                self.tray = background.Tray(post=lambda a: self.q.put(("tray", a)), status=self._schedule_text)
            except Exception:  # noqa: BLE001 - no tray available: behave like a normal window
                self.tray = None

    def _schedule_text(self) -> str:
        s = self.settings
        nd = background.next_due(s.auto_check, s.auto_time, s.auto_weekday,
                                 datetime.fromisoformat(s.last_auto_run) if s.last_auto_run else None)
        return f"Next check: {nd:%a %d %b, %H:%M}" if nd else "Automatic checks: off"

    def _tray_action(self, action: str):
        if action == "show":
            self.deiconify()
            self.lift()
            self.focus_force()
        elif action == "check":
            self._start(auto=True)
        elif action == "quit":
            self._quit()

    def _on_close(self):
        if self.settings.tray_on_close or self.settings.auto_check != "off":
            self._ensure_tray()
            if self.tray:
                self.withdraw()
                if not getattr(self, "_tray_hint_shown", False):
                    self.tray.notify("RegWatch is still running",
                                     "It keeps checking on schedule. Right-click the tray icon to quit.")
                    self._tray_hint_shown = True
                return
        self._quit()

    def _quit(self):
        if self.tray:
            self.tray.stop()
        self.destroy()

    def _tick(self):
        """Every minute: start a scheduled check when one is due."""
        try:
            s = self.settings
            last = datetime.fromisoformat(s.last_auto_run) if s.last_auto_run else None
            nd = background.next_due(s.auto_check, s.auto_time, s.auto_weekday, last)
            if nd and nd <= datetime.now() and not self.running:
                self._start(auto=True)
            if self.tray:
                self.tray.refresh()
        finally:
            self.after(60_000, self._tick)

    def _new_summary(self, res) -> str:
        try:
            from collections import Counter

            from .store import Store
            st = Store(Path(self.out_var.get()) / "history.db")
            try:
                _, _, ts = st.last_run()
                c = Counter(r["regulator"] for r in st.all_rows()
                            if r["first_seen"] and r["first_seen"].isoformat(timespec="seconds") == ts)
            finally:
                st.close()
            top = ", ".join(f"{k} {v}" for k, v in c.most_common(4))
            return f"{res.new_count} new update{'s' if res.new_count != 1 else ''}: {top}"
        except Exception:  # noqa: BLE001
            return f"{res.new_count} new updates"

    # ----- app updates -----
    def _check_update(self):
        threading.Thread(target=lambda: self.q.put(("update", updater.latest())), daemon=True).start()

    def _show_update(self, rel):
        if not rel:
            return
        self._release = rel
        self.banner_lbl.configure(text=f"RegWatch {rel.version} is available  (you have {__version__})")
        self.banner_btn.configure(state="normal" if rel.exe_url else "disabled", text="Download",
                                  command=self._download_update)
        self.banner.grid(row=0, column=0, sticky="ew", pady=(0, 12))

    def _download_update(self):
        rel = self._release
        if not rel or not rel.exe_url:
            return
        self.banner_btn.configure(state="disabled", text="Downloading…")
        dest = Path(os.environ.get("USERPROFILE", Path.home())) / "Downloads"

        def work():
            try:
                self.q.put(("update_dl", updater.download(rel, dest)))
            except Exception as e:  # noqa: BLE001
                self.q.put(("update_dl", e))

        threading.Thread(target=work, daemon=True).start()

    def _update_downloaded(self, result):
        if isinstance(result, Path):
            self.banner_lbl.configure(text=f"Downloaded {result.name}. Close RegWatch and run the new file.")
            self.banner_btn.configure(state="normal", text="Show file",
                                      command=lambda: subprocess.Popen(["explorer", "/select,", str(result)]))
        else:
            self.banner_lbl.configure(text=f"Download failed ({result}). Use 'What's new' to download it manually.")
            self.banner_btn.configure(state="normal", text="Retry", command=self._download_update)

    # ----- digest -----
    def _make_digest(self):
        out = Path(self.out_var.get())
        if not (out / "history.db").exists():
            self.status.configure(text="Fetch updates first, then make a digest.", text_color=MUTED)
            return
        since, until = self._since(), date.today()
        path = filedialog.asksaveasfilename(
            parent=self, title="Save digest PDF", defaultextension=".pdf", initialdir=str(out),
            initialfile=f"RegWatch Digest {since:%d %b} - {until:%d %b %Y}.pdf", filetypes=[("PDF", "*.pdf")])
        if not path:
            return
        self.digest_btn.configure(state="disabled", text="Building…")

        def work():
            try:
                from .digest import build_digest
                from .store import Store
                st = Store(out / "history.db")
                try:
                    rows = st.all_rows()
                finally:
                    st.close()
                try:
                    from .dedupe import merge_duplicates
                    rows = merge_duplicates(rows)
                except ImportError:
                    pass
                self.q.put(("digest", build_digest(rows, since, until, Path(path))))
            except Exception as e:  # noqa: BLE001
                self.q.put(("digest", f"{type(e).__name__}: {e}"))

        threading.Thread(target=work, daemon=True).start()

    # ----- settings -----
    def _settings_dialog(self):
        s = self.settings
        win = ctk.CTkToplevel(self)
        win.title("Settings")
        win.geometry("470x590")
        win.resizable(False, False)
        win.configure(fg_color=BG)
        win.transient(self)
        win.after(50, win.grab_set)
        win.after(250, lambda: win.iconbitmap(str(resource("assets/regwatch.ico"))))
        card = Card(win)
        card.pack(fill="both", expand=True, padx=16, pady=(16, 10))

        def head(t):
            ctk.CTkLabel(card, text=t, font=font(11, "bold"), text_color=MUTED, anchor="w").pack(fill="x", padx=18, pady=(16, 6))

        head("AUTOMATIC CHECKS")
        mode = ctk.CTkSegmentedButton(card, values=["Off", "Daily", "Weekly"], font=font(12),
                                      selected_color=ACCENT, selected_hover_color=ACCENT_HOVER)
        mode.set(s.auto_check.title())
        mode.pack(fill="x", padx=16)
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=(8, 0))
        menu = dict(height=34, corner_radius=10, fg_color=CARD_ALT, button_color=CARD_ALT, button_hover_color=ACCENT_SOFT,
                    text_color=TEXT, dropdown_fg_color=CARD, dropdown_text_color=TEXT, dropdown_hover_color=ACCENT_SOFT,
                    font=font(12), dropdown_font=font(12))
        day = ctk.CTkOptionMenu(row, values=background.WEEKDAYS, width=150, **menu)
        day.set(background.WEEKDAYS[s.auto_weekday])
        day.pack(side="left")
        times = [f"{h:02d}:00" for h in range(6, 23)]
        at = ctk.CTkOptionMenu(row, values=times, width=100, **menu)
        at.set(s.auto_time if s.auto_time in times else "09:00")
        at.pack(side="left", padx=8)
        hint = ctk.CTkLabel(card, text="", font=font(11), text_color=MUTED, anchor="w", justify="left", wraplength=390)
        hint.pack(fill="x", padx=18, pady=(6, 0))

        def sync(*_):
            m = mode.get().lower()
            day.configure(state="normal" if m == "weekly" else "disabled")
            at.configure(state="normal" if m != "off" else "disabled")
            hint.configure(text=("Checks the sources ticked on the Sources page while RegWatch is open or in the "
                                 "tray, and catches up if the PC was off at that time." if m != "off"
                                 else "Only when you click 'Fetch updates'."))
        mode.configure(command=sync)
        sync()

        head("BACKGROUND")
        sw = dict(font=font(12), text_color=TEXT, progress_color=ACCENT)
        v_tray = ctk.BooleanVar(value=s.tray_on_close)
        ctk.CTkSwitch(card, text="Keep running in the tray when I close the window", variable=v_tray, **sw).pack(anchor="w", padx=18, pady=4)
        try:
            auto_on = background.autostart_enabled()
        except Exception:  # noqa: BLE001
            auto_on = False
        v_start = ctk.BooleanVar(value=auto_on)
        ctk.CTkSwitch(card, text="Start with Windows (in the tray)", variable=v_start, **sw).pack(anchor="w", padx=18, pady=4)
        v_notify = ctk.BooleanVar(value=s.notify)
        ctk.CTkSwitch(card, text="Notify me when a scheduled check finds updates", variable=v_notify, **sw).pack(anchor="w", padx=18, pady=4)

        head("APP UPDATES")
        v_upd = ctk.BooleanVar(value=s.check_updates)
        ctk.CTkSwitch(card, text="Check GitHub for a newer RegWatch at start-up", variable=v_upd, **sw).pack(anchor="w", padx=18, pady=(4, 16))

        def save():
            s.auto_check = mode.get().lower()
            s.auto_weekday = background.WEEKDAYS.index(day.get())
            s.auto_time = at.get()
            s.tray_on_close, s.notify, s.check_updates = v_tray.get(), v_notify.get(), v_upd.get()
            if s.auto_check != "off" and not s.last_auto_run:
                s.last_auto_run = datetime.now().isoformat(timespec="seconds")  # don't fire the moment it's enabled
            s.save()
            try:
                background.set_autostart(v_start.get())
            except Exception:  # noqa: BLE001
                pass
            if v_start.get() or s.auto_check != "off":
                self._ensure_tray()
            if self.tray:
                self.tray.refresh()
            self.status.configure(text=f"Settings saved. {self._schedule_text()}.", text_color=OK)
            win.destroy()

        foot = ctk.CTkFrame(win, fg_color="transparent")
        foot.pack(fill="x", padx=16, pady=(0, 16))
        ctk.CTkButton(foot, text="Save", width=110, height=36, corner_radius=10, fg_color=ACCENT, hover_color=ACCENT_HOVER,
                      font=font(13, "bold"), command=save).pack(side="right")
        ctk.CTkButton(foot, text="Cancel", width=90, height=36, corner_radius=10, fg_color="transparent", border_width=1,
                      border_color=BORDER, text_color=TEXT, hover_color=ACCENT_SOFT, font=font(12),
                      command=win.destroy).pack(side="right", padx=8)

    def _about(self):
        win = ctk.CTkToplevel(self)
        win.title("About RegWatch")
        win.geometry("560x520")
        win.configure(fg_color=BG)
        win.transient(self)
        win.after(50, win.grab_set)
        box = ctk.CTkTextbox(win, wrap="word", font=font(12), fg_color=CARD, border_width=1, border_color=BORDER, corner_radius=12)
        box.pack(fill="both", expand=True, padx=16, pady=(16, 8))
        box.insert("end", (
            f"RegWatch {__version__}: free and open source (MIT)\n\n"
            "Reads the public listing pages of Indian regulators and builds an Excel tracker. Titles, dates and "
            "links are as published by each regulator; always rely on the official document.\n\n"
            "• Topics are keyword-based. Edit tag_rules.json in the output folder to tune them.\n"
            "• 'Posted late' marks a document that appeared after an earlier run had already covered its date.\n"
            "• Status / Owner / Notes columns in the tracker are kept across refreshes.\n\n"
            "Run on a schedule (e.g. every Monday) with Windows Task Scheduler:\n"
            "    RegWatch.exe --cli --days 7\n\n"
            "Not covered automatically (the site blocks automated access) - check manually:\n"
            "• mca.gov.in notifications & circulars (gazetted MCA rules are covered where available)\n"
            "• the full CBDT list on incometaxindia.gov.in\n"
        ))
        box.configure(state="disabled")
        ctk.CTkButton(win, text="Close", fg_color=ACCENT, hover_color=ACCENT_HOVER, command=win.destroy).pack(pady=(0, 16))


def main(start_hidden: bool = False):
    try:  # own taskbar identity, so Windows shows the RegWatch icon instead of python.exe's
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("RegWatch.Desktop")
    except Exception:  # noqa: BLE001 - non-Windows / older Windows
        pass
    App(start_hidden=start_hidden).mainloop()
