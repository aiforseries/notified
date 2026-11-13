"""In-app results browser: searchable/filterable list of fetched items with a detail panel
(open page/PDF, set Status / Owner / Notes)."""
from __future__ import annotations

import os
import webbrowser
from pathlib import Path
from tkinter import ttk

import customtkinter as ctk

from .excel import STATUS_CHOICES
from .runner import TRACKER_NAME
from .store import Store
from .ui_kit import ACCENT, ACCENT_HOVER, ACCENT_SOFT, AMBER, BORDER, CARD, CARD_ALT, MUTED, TEXT, Card, Pill, font

STATUS_SHORT = {"To review": "To review", "Relevant - action needed": "Action", "Reviewed / noted": "Noted",
                "Not relevant": "Not relevant"}
SHORT_TO_FULL = {v: k for k, v in STATUS_SHORT.items()}
ALL_REG, ALL_TOPIC = "All regulators", "All topics"
HIDE_TOPICS = ("Students / Exams", "Internal / Admin")


def _mode_color(pair):
    return pair[1] if ctk.get_appearance_mode() == "Dark" else pair[0]


class ResultsView(ctk.CTkFrame):
    def __init__(self, master, out_dir_getter, sources_by_id):
        super().__init__(master, fg_color="transparent")
        self.out_dir = out_dir_getter
        self.sources = sources_by_id
        self.rows: list[dict] = []
        self.by_uid: dict[str, dict] = {}
        self.user: dict[str, dict] = {}
        self.snippets: dict[str, str] = {}
        self.last_uids: set[str] = set()
        self.current: dict | None = None
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self._toolbar()
        self._body()

    # ---------- layout ----------
    def _toolbar(self):
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        self.q = ctk.CTkEntry(bar, placeholder_text="Search titles and PDF text…  (Enter)", height=36, corner_radius=10,
                              border_color=BORDER, fg_color=CARD, font=font(12))
        self.q.pack(side="left", fill="x", expand=True)
        self.q.bind("<Return>", lambda e: self.refresh())
        self.q.bind("<Escape>", lambda e: (self.q.delete(0, "end"), self.refresh()))
        menu = dict(height=36, corner_radius=10, fg_color=CARD, button_color=CARD, button_hover_color=ACCENT_SOFT,
                    text_color=TEXT, dropdown_fg_color=CARD, dropdown_text_color=TEXT, dropdown_hover_color=ACCENT_SOFT,
                    font=font(12), dropdown_font=font(12), dynamic_resizing=False)
        self.reg = ctk.CTkOptionMenu(bar, values=[ALL_REG], width=150, command=lambda _: self.refresh(), **menu)
        self.reg.pack(side="left", padx=(8, 0))
        self.topic = ctk.CTkOptionMenu(bar, values=[ALL_TOPIC], width=190, command=lambda _: self.refresh(), **menu)
        self.topic.pack(side="left", padx=(8, 0))
        self.scope = ctk.CTkSegmentedButton(bar, values=["Latest run", "All history"], height=36, font=font(12),
                                            selected_color=ACCENT, selected_hover_color=ACCENT_HOVER,
                                            command=lambda _: self.refresh())
        self.scope.set("Latest run")
        self.scope.pack(side="left", padx=(8, 0))

    def _body(self):
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew")
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(0, weight=1)

        listcard = Card(body)
        listcard.grid(row=0, column=0, sticky="nsew")
        listcard.grid_columnconfigure(0, weight=1)
        listcard.grid_rowconfigure(1, weight=1)
        self.count = ctk.CTkLabel(listcard, text="", font=font(11), text_color=MUTED, anchor="w")
        self.count.grid(row=0, column=0, sticky="ew", padx=14, pady=(10, 2))
        self.style = ttk.Style(self)
        self.tree = ttk.Treeview(listcard, columns=("date", "reg", "title", "status"), show="headings",
                                 style="RW.Treeview", selectmode="browse")
        self.COLS = (("date", "Date", 84, False), ("reg", "Regulator", 78, False),
                     ("title", "Title", 160, True), ("status", "Status", 92, False))
        for col, text, w, stretch in self.COLS:
            self.tree.heading(col, text=text, anchor="w", command=lambda c=col: self._sort(c))
            self.tree.column(col, width=w, minwidth=40, stretch=stretch, anchor="w")
        sb = ctk.CTkScrollbar(listcard, command=self.tree.yview, button_color=BORDER)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.grid(row=1, column=0, sticky="nsew", padx=(10, 0), pady=(0, 10))
        sb.grid(row=1, column=1, sticky="ns", padx=(0, 6), pady=(0, 10))
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._select())
        self.tree.bind("<Double-1>", lambda e: self._open_page())
        self.tree.bind("<Return>", lambda e: self._open_page())
        self.empty = ctk.CTkLabel(listcard, text="", font=font(13), text_color=MUTED)

        self.detail = Card(body, width=330)
        self.detail.grid(row=0, column=1, sticky="nsew", padx=(12, 0))
        self.detail.grid_propagate(False)
        self.detail.grid_columnconfigure(0, weight=1)
        self.detail.grid_rowconfigure(0, weight=1)
        d = ctk.CTkScrollableFrame(self.detail, fg_color="transparent", scrollbar_button_color=BORDER)
        d.grid(row=0, column=0, sticky="nsew", padx=2, pady=2)
        self.d_meta = ctk.CTkLabel(d, text="Select an item", font=font(11, "bold"), text_color=MUTED, anchor="w", justify="left")
        self.d_meta.pack(fill="x", padx=16, pady=(16, 4))
        self.d_title = ctk.CTkLabel(d, text="", font=font(14, "bold"), text_color=TEXT, anchor="w", justify="left", wraplength=280)
        self.d_title.pack(fill="x", padx=16)
        self.d_tags = ctk.CTkFrame(d, fg_color="transparent")
        self.d_tags.pack(fill="x", padx=14, pady=(8, 0))
        self.d_snip = ctk.CTkLabel(d, text="", font=font(11), text_color=MUTED, anchor="w", justify="left", wraplength=290)
        self.d_snip.pack(fill="x", padx=16, pady=(6, 0))
        btns = ctk.CTkFrame(d, fg_color="transparent")
        btns.pack(fill="x", padx=14, pady=(12, 6))
        prim = dict(height=34, corner_radius=10, font=font(12, "bold"), fg_color=ACCENT, hover_color=ACCENT_HOVER)
        ghost = dict(height=34, corner_radius=10, font=font(12), fg_color="transparent", border_width=1,
                     border_color=BORDER, text_color=TEXT, hover_color=ACCENT_SOFT)
        self.b_page = ctk.CTkButton(btns, text="Open page", width=100, command=self._open_page, **prim)
        self.b_page.pack(side="left", padx=2)
        self.b_pdf = ctk.CTkButton(btns, text="PDF", width=60, command=self._open_pdf, **ghost)
        self.b_pdf.pack(side="left", padx=2)
        self.b_copy = ctk.CTkButton(btns, text="Copy ref", width=80, command=self._copy, **ghost)
        self.b_copy.pack(side="left", padx=2)

        ctk.CTkLabel(d, text="STATUS", font=font(10, "bold"), text_color=MUTED, anchor="w").pack(fill="x", padx=16, pady=(12, 4))
        self.d_status = ctk.CTkSegmentedButton(d, values=list(STATUS_SHORT.values()), font=font(11),
                                               selected_color=ACCENT, selected_hover_color=ACCENT_HOVER,
                                               command=lambda _: self._save())
        self.d_status.pack(fill="x", padx=14)
        ctk.CTkLabel(d, text="OWNER", font=font(10, "bold"), text_color=MUTED, anchor="w").pack(fill="x", padx=16, pady=(10, 4))
        self.d_owner = ctk.CTkEntry(d, height=32, corner_radius=8, border_color=BORDER, fg_color=CARD_ALT, font=font(12))
        self.d_owner.pack(fill="x", padx=14)
        self.d_owner.bind("<FocusOut>", lambda e: self._save())
        self.d_owner.bind("<Return>", lambda e: self._save())
        ctk.CTkLabel(d, text="NOTES", font=font(10, "bold"), text_color=MUTED, anchor="w").pack(fill="x", padx=16, pady=(10, 4))
        self.d_notes = ctk.CTkTextbox(d, height=90, corner_radius=8, border_width=1, border_color=BORDER, fg_color=CARD_ALT,
                                      font=font(12), wrap="word")
        self.d_notes.pack(fill="x", padx=14)
        self.d_notes.bind("<FocusOut>", lambda e: self._save())
        self.d_saved = ctk.CTkLabel(d, text="Saved to the tracker on the next run or 'Update tracker'.", font=font(10),
                                    text_color=MUTED, anchor="w", justify="left", wraplength=290)
        self.d_saved.pack(fill="x", padx=16, pady=(6, 14))
        self._enable_detail(False)

    # ---------- theme ----------
    def restyle(self):
        bg, fg, muted, sel = _mode_color(CARD), _mode_color(TEXT), _mode_color(MUTED), _mode_color(ACCENT_SOFT)
        k = self._get_widget_scaling()  # ttk doesn't follow Windows display scaling like CustomTkinter does
        for col, _, w, _ in self.COLS:
            self.tree.column(col, width=int(w * k), minwidth=int(40 * k))
        self.style.theme_use("clam")
        self.style.configure("RW.Treeview", background=bg, fieldbackground=bg, foreground=fg, rowheight=int(26 * k),
                             borderwidth=0, font=("Segoe UI", 10))
        self.style.configure("RW.Treeview.Heading", background=bg, foreground=muted, borderwidth=0, relief="flat",
                             font=("Segoe UI", 9, "bold"), padding=(6, 6))
        self.style.map("RW.Treeview.Heading", background=[("active", bg)])
        self.style.map("RW.Treeview", background=[("selected", sel)], foreground=[("selected", fg)])
        self.style.layout("RW.Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
        self.tree.tag_configure("new", foreground=_mode_color(ACCENT), font=("Segoe UI", 10, "bold"))
        self.tree.tag_configure("late", foreground=_mode_color(AMBER))

    # ---------- data ----------
    def load(self):
        """Reload rows from the output folder's history (call after a run or when the view is opened)."""
        out = Path(self.out_dir())
        db = out / "history.db"
        self.rows, self.by_uid, self.user, self.last_uids, self.last_ts = [], {}, {}, set(), ""
        if db.exists():
            store = Store(db)
            try:
                rows = store.all_rows()
                try:
                    from .dedupe import merge_duplicates
                    rows = merge_duplicates(rows)
                except ImportError:
                    pass
                from .usercols import merged
                self.user = merged(store, out / TRACKER_NAME)
                self.last_uids, _, self.last_ts = store.last_run()
            finally:
                store.close()
            self.rows = rows
            self.by_uid = {r["uid"]: r for r in rows}
        regs = sorted({r["regulator"] for r in self.rows})
        topics = sorted({t for r in self.rows for t in r["tags"]})
        self.reg.configure(values=[ALL_REG] + regs)
        self.topic.configure(values=[ALL_TOPIC] + topics)
        if self.reg.get() not in [ALL_REG] + regs:
            self.reg.set(ALL_REG)
        if self.topic.get() not in [ALL_TOPIC] + topics:
            self.topic.set(ALL_TOPIC)
        if not self.last_uids:
            self.scope.set("All history")
        self.restyle()
        self.refresh()

    def _matches(self) -> list[dict]:
        q = self.q.get().strip()
        rows = self.rows
        if self.scope.get() == "Latest run" and self.last_uids:
            rows = [r for r in rows if r["uid"] in self.last_uids or any(a.get("uid") in self.last_uids for a in r.get("also", []))]
        if self.reg.get() != ALL_REG:
            rows = [r for r in rows if r["regulator"] == self.reg.get()]
        if self.topic.get() != ALL_TOPIC:
            rows = [r for r in rows if self.topic.get() in r["tags"]]
        elif not q:
            rows = [r for r in rows if not (set(r["tags"]) & set(HIDE_TOPICS))]
        self.snippets = {}
        if q:
            hits: dict[str, str] = {}
            try:
                from .search import PdfIndex
                for h in PdfIndex(Path(self.out_dir()) / "history.db").search(q, limit=500):
                    hits[h["uid"]] = h.get("snippet", "")
            except Exception:  # noqa: BLE001 - search index optional / not built yet
                pass
            ql = q.lower()
            rows = [r for r in rows if r["uid"] in hits or ql in f"{r['title']} {r['ref_no']}".lower()]
            self.snippets = hits
        return rows

    def refresh(self):
        rows = self._matches()
        self.tree.delete(*self.tree.get_children())
        for r in rows:
            st = self._u(r).get("Status", "")
            tags = ("new",) if self._is_new(r) else ()
            if "Posted late" in r["tags"]:
                tags += ("late",)
            self.tree.insert("", "end", iid=r["uid"], tags=tags, values=(
                r["date"].strftime("%d-%m-%y") if r["date"] else "—", r["regulator"],
                r["title"] + (f"   ·  +{len(r['also'])} more" if r.get("also") else ""), STATUS_SHORT.get(st, st)))
        hidden = "" if self.topic.get() != ALL_TOPIC or self.q.get().strip() else "  ·  student/admin notices hidden"
        self.count.configure(text=f"{len(rows)} item{'s' if len(rows) != 1 else ''}{hidden}")
        if not self.rows:
            self.empty.configure(text="Nothing here yet. Pick sources and click 'Fetch updates'.")
            self.empty.grid(row=1, column=0)
        elif not rows:
            self.empty.configure(text="No items match these filters.")
            self.empty.grid(row=1, column=0)
        else:
            self.empty.grid_forget()
        if rows:
            self.tree.selection_set(rows[0]["uid"])
            self.tree.see(rows[0]["uid"])
        else:
            self._enable_detail(False)

    def _u(self, r) -> dict:
        """Status/Owner/Notes for a (possibly merged) row: the primary's own, else the first member that has some."""
        for uid in r.get("member_uids") or [r["uid"]]:
            if self.user.get(uid):
                return self.user[uid]
        return {}

    def _is_new(self, r) -> bool:
        """First seen in the latest run."""
        fs = r.get("first_seen")
        return bool(fs and self.last_ts and fs.isoformat(timespec="seconds") == self.last_ts)

    def _sort(self, col):
        key = {"date": lambda r: r["date"].toordinal() if r["date"] else 0, "reg": lambda r: r["regulator"],
               "title": lambda r: r["title"].lower(),
               "status": lambda r: self._u(r).get("Status", "")}[col]
        rev = getattr(self, "_sort_state", (None, False))
        desc = not rev[1] if rev[0] == col else col == "date"
        self._sort_state = (col, desc)
        order = sorted((self.by_uid[i] for i in self.tree.get_children()), key=key, reverse=desc)
        for n, r in enumerate(order):
            self.tree.move(r["uid"], "", n)

    # ---------- detail ----------
    def _enable_detail(self, on: bool):
        state = "normal" if on else "disabled"
        for w in (self.b_page, self.b_pdf, self.b_copy, self.d_status, self.d_owner):
            w.configure(state=state)
        if not on:
            self.current = None
            self.d_meta.configure(text="Select an item")
            self.d_title.configure(text="")
            self.d_snip.configure(text="")
            for w in self.d_tags.winfo_children():
                w.destroy()

    def _select(self):
        sel = self.tree.selection()
        if not sel:
            return
        r = self.by_uid.get(sel[0])
        if not r:
            return
        self.current = r
        self._enable_detail(True)
        src = self.sources.get(r["source_id"])
        bits = [r["date"].strftime("%d %b %Y") if r["date"] else "Undated", r["regulator"], r["doc_type"]]
        self.d_meta.configure(text="  ·  ".join(b for b in bits if b) + (f"\n{r['ref_no']}" if r["ref_no"] else ""))
        self.d_title.configure(text=r["title"])
        for w in self.d_tags.winfo_children():
            w.destroy()
        for t in r["tags"][:4]:
            Pill(self.d_tags, t, fg=CARD_ALT, color=MUTED).pack(side="left", padx=2, pady=2)
        also = r.get("also") or []
        extra = ("Also at: " + ", ".join(a.get("regulator") or a.get("source_id", "") for a in also)) if also else ""
        snip = self.snippets.get(r["uid"], "")
        self.d_snip.configure(text="\n".join(x for x in (extra, f"“{snip}”" if snip else "",
                                                          f"Source: {src.name}" if src else "") if x))
        has_pdf = bool(r.get("local_pdf") or r.get("pdf_url"))
        self.b_pdf.configure(state="normal" if has_pdf else "disabled")
        self.b_copy.configure(state="normal" if r["ref_no"] else "disabled")
        u = self._u(r)
        self.d_status.set(STATUS_SHORT.get(u.get("Status", ""), ""))
        self.d_owner.delete(0, "end")
        self.d_owner.insert(0, u.get("Owner", "") or "")
        self.d_notes.delete("1.0", "end")
        self.d_notes.insert("1.0", u.get("Notes", "") or "")
        self._loaded_vals = self._vals()

    def _vals(self):
        return (SHORT_TO_FULL.get(self.d_status.get(), ""), self.d_owner.get().strip(), self.d_notes.get("1.0", "end").strip())

    def _save(self):
        if not self.current:
            return
        vals = self._vals()
        if vals == getattr(self, "_loaded_vals", None):
            return
        status, owner, notes = vals
        if status and status not in STATUS_CHOICES:
            status = ""
        store = Store(Path(self.out_dir()) / "history.db")
        try:
            store.set_user_meta(self.current["uid"], status=status, owner=owner, notes=notes)
        finally:
            store.close()
        self.user[self.current["uid"]] = {"Status": status, "Owner": owner, "Notes": notes}
        self._loaded_vals = vals
        if self.tree.exists(self.current["uid"]):
            self.tree.set(self.current["uid"], "status", STATUS_SHORT.get(status, status))
        self.d_saved.configure(text="Saved. Written to the Excel tracker on the next run or 'Update tracker'.")

    def _open_page(self):
        if self.current and self.current.get("url"):
            webbrowser.open(self.current["url"])

    def _open_pdf(self):
        if not self.current:
            return
        lp = self.current.get("local_pdf")
        if lp and Path(lp).exists():
            os.startfile(lp)  # noqa: S606 - user's own downloaded file
        elif self.current.get("pdf_url"):
            webbrowser.open(self.current["pdf_url"])

    def _copy(self):
        if self.current and self.current.get("ref_no"):
            self.clipboard_clear()
            self.clipboard_append(self.current["ref_no"])
            self.d_saved.configure(text=f"Copied: {self.current['ref_no']}")
