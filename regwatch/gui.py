"""Desktop window (CustomTkinter): sidebar with period / output / theme, source cards, live progress, result tiles."""
from __future__ import annotations

import calendar
import os
import queue
import re
import threading
from datetime import date, timedelta
from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk

from . import __version__
from .paths import resource
from .runner import TRACKER_NAME, Settings, run
from .sources import all_sources

# ---------- design tokens: (light, dark) ----------
BG = ("#F3F5F9", "#0B1220")
CARD = ("#FFFFFF", "#121B2E")
CARD_ALT = ("#F8FAFC", "#0F172A")
BORDER = ("#E2E8F0", "#1F2A40")
TEXT = ("#0F172A", "#E2E8F0")
MUTED = ("#64748B", "#94A3B8")
ACCENT = ("#0F766E", "#14B8A6")
ACCENT_HOVER = ("#115E59", "#0D9488")
ACCENT_SOFT = ("#CCFBF1", "#123B3A")
AMBER = ("#B45309", "#FBBF24")
DANGER = ("#B91C1C", "#F87171")
OK = ("#15803D", "#4ADE80")

PRESETS = [("7 days", 7), ("30 days", 30), ("90 days", 90), ("This FY", -1), ("Custom", 0)]


def fy_start(today: date) -> date:
    return date(today.year if today.month >= 4 else today.year - 1, 4, 1)


def font(size=13, weight="normal"):
    return ctk.CTkFont(family="Segoe UI", size=size, weight=weight)


# ---------- small widgets ----------
class Card(ctk.CTkFrame):
    def __init__(self, master, **kw):
        super().__init__(master, fg_color=CARD, corner_radius=14, border_width=1, border_color=BORDER, **kw)


class Pill(ctk.CTkLabel):
    def __init__(self, master, text, fg=ACCENT_SOFT, color=ACCENT, **kw):
        super().__init__(master, text=text, fg_color=fg, text_color=color, corner_radius=9,
                         font=font(11, "bold"), padx=8, height=20, **kw)


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
    def __init__(self):
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

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self._sidebar()
        self._main()
        self._refresh_period()
        self._update_count()
        self.after(120, self._drain)

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
        ctk.CTkButton(sb, text=f"About  ·  v{__version__}", fg_color="transparent", hover_color=ACCENT_SOFT,
                      text_color=MUTED, font=font(11), height=26, command=self._about).grid(row=7, column=0, pady=(0, 14))

    def _section(self, parent, row, text):
        ctk.CTkLabel(parent, text=text, font=font(11, "bold"), text_color=MUTED, anchor="w").grid(
            row=row, column=0, sticky="ew", padx=26, pady=(16, 6))

    # ----- main panel -----
    def _main(self):
        main = ctk.CTkFrame(self, fg_color="transparent")
        main.grid(row=0, column=1, sticky="nsew", padx=24, pady=(22, 10))
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(1, weight=1)

        top = ctk.CTkFrame(main, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        ctk.CTkLabel(top, text="Sources", font=font(22, "bold"), text_color=TEXT).pack(side="left")
        self.count_pill = Pill(top, "")
        self.count_pill.pack(side="left", padx=10, pady=(4, 0))
        self.search = ctk.CTkEntry(top, placeholder_text="Filter sources…", width=220, height=34, corner_radius=10,
                                   border_color=BORDER, fg_color=CARD, font=font(12))
        self.search.pack(side="right")
        self.search.bind("<KeyRelease>", lambda e: self._filter())
        ghost = dict(height=34, width=70, corner_radius=10, fg_color="transparent", border_width=1, border_color=BORDER,
                     text_color=TEXT, hover_color=ACCENT_SOFT, font=font(12))
        ctk.CTkButton(top, text="None", command=lambda: self._set_all(False), **ghost).pack(side="right", padx=(0, 8))
        ctk.CTkButton(top, text="All", command=lambda: self._set_all(True), **ghost).pack(side="right", padx=(0, 6))

        scroll = ctk.CTkScrollableFrame(main, fg_color="transparent", scrollbar_button_color=BORDER)
        scroll.grid(row=1, column=0, sticky="nsew")
        scroll.grid_columnconfigure((0, 1), weight=1, uniform="c")

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
        bar.grid(row=2, column=0, sticky="ew", pady=(12, 0))
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
        ctk.CTkButton(btns, text="Open tracker", width=120, command=self._open_tracker, **ghost).pack(side="left", padx=4)
        ctk.CTkButton(btns, text="Folder", width=80, command=lambda: self._open(Path(self.out_var.get())), **ghost).pack(side="left", padx=4)
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
    def _start(self):
        ids = [i for i, v in self.src_vars.items() if v.get()]
        if not ids:
            self.status.configure(text="Tick at least one source.", text_color=DANGER)
            return
        since = self._since()
        pdf_regs = [r for r, v in self.pdf_vars.items() if v.get()]
        out = Path(self.out_var.get()).expanduser()
        self.settings.days = dict(PRESETS)[self.preset.get()] or 30
        self.settings.enabled, self.settings.pdf_regulators, self.settings.output_dir = ids, pdf_regs, str(out)
        self.settings.known_ids = list(self.src_vars)
        self.settings.save()
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
                    self._reset_buttons()
                    self.status.configure(text=f"Failed: {payload}", text_color=DANGER)
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
            self._open(p)
        else:
            self.status.configure(text="No tracker yet. Click 'Fetch updates' first.", text_color=MUTED)

    @staticmethod
    def _open(p: Path):
        if not p.suffix:
            p.mkdir(parents=True, exist_ok=True)
        os.startfile(str(p))  # noqa: S606 - Windows only, user-chosen path

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


def main():
    try:  # own taskbar identity, so Windows shows the RegWatch icon instead of python.exe's
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("RegWatch.Desktop")
    except Exception:  # noqa: BLE001 - non-Windows / older Windows
        pass
    App().mainloop()
