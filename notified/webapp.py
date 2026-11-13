"""Notified desktop window: HTML/CSS UI in Windows' Edge WebView2 (pywebview), backed by the Python engine.

JS calls `window.pywebview.api.<method>()`; Python pushes events with `RW.emit(name, payload)`."""
from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import webbrowser
from datetime import date, datetime, timedelta
from pathlib import Path

import webview

from . import __version__, background, updater
from .excel import STATUS_CHOICES
from .paths import resource
from .runner import TRACKER_NAME, Settings, migrate_tracker, rewrite_tracker, run
from .sources import all_sources
from .store import Store

log = logging.getLogger("notified.web")

from .palette import FILE_TABS  # noqa: E402
from .tagger import NOISE_TAGS as NOISE  # noqa: E402


def _fy_start(t: date) -> date:
    return date(t.year if t.month >= 4 else t.year - 1, 4, 1)


class Api:
    """Methods exposed to the page. Keep return values JSON-friendly."""

    def __init__(self):
        self._s = Settings.load()
        self._sources = all_sources()
        self._by_id = {s.id: s for s in self._sources}
        self._window: webview.Window | None = None
        self._tray: background.Tray | None = None
        self._running = False
        self._cancel = False
        self._release: updater.Release | None = None
        self._quitting = False

    # ---------- plumbing ----------
    def _emit(self, name: str, payload=None):
        if self._window:
            try:
                self._window.evaluate_js(f"RW.emit({json.dumps(name)}, {json.dumps(payload, default=str)})")
            except Exception:  # noqa: BLE001 - window closing
                pass

    def _out(self) -> Path:
        return Path(self._s.output_dir).expanduser()

    def _enabled(self) -> set[str]:
        defaults = {s.id for s in self._sources if s.enabled_by_default}
        s = self._s
        if s.enabled:  # saved choice + default-on sources added since then
            return set(s.enabled) | (defaults - set(s.known_ids or s.enabled))
        return defaults

    # ---------- start-up ----------
    def bootstrap(self):
        s = self._s
        enabled = self._enabled()
        groups: dict[str, dict] = {}
        for src in self._sources:
            g = groups.setdefault(src.group, {"name": src.group, "regulators": [], "sources": []})
            if src.regulator not in g["regulators"]:
                g["regulators"].append(src.regulator)
            g["sources"].append({"id": src.id, "name": src.name, "regulator": src.regulator,
                                 "on": src.id in enabled, "highVolume": not src.enabled_by_default,
                                 "homepage": src.homepage})
        last = self._last_run_info()
        return {
            "version": __version__, "groups": list(groups.values()), "pdfRegulators": s.pdf_regulators,
            "fileTabs": FILE_TABS, "statuses": STATUS_CHOICES,
            "settings": {"outputDir": s.output_dir, "days": s.days, "theme": s.theme, "autoCheck": s.auto_check,
                         "autoTime": s.auto_time, "autoWeekday": s.auto_weekday, "trayOnClose": s.tray_on_close,
                         "notify": s.notify, "checkUpdates": s.check_updates, "startWithWindows": self._autostart()},
            "schedule": self._schedule_text(), "lastRun": last, "today": date.today().isoformat(),
            "fyStart": _fy_start(date.today()).isoformat(),
        }

    def _autostart(self) -> bool:
        try:
            return background.autostart_enabled()
        except Exception:  # noqa: BLE001
            return False

    def _last_run_info(self):
        db = self._out() / "history.db"
        if not db.exists():
            return None
        st = Store(db)
        try:
            uids, since, ts = st.last_run()
            return {"at": ts, "since": since.isoformat() if since else None, "count": len(uids)}
        finally:
            st.close()

    # ---------- window chrome ----------
    def _is_dark(self) -> bool:
        t = self._s.theme
        if t in ("light", "dark"):
            return t == "dark"
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
                return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 0
        except OSError:
            return False

    def _titlebar(self):
        """Match the native title bar to the app theme (Windows 10 2004+ / 11)."""
        try:
            import ctypes
            hwnd = self._window.native.Handle.ToInt64()
            val = ctypes.c_int(1 if self._is_dark() else 0)
            for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (new, old id)
                if ctypes.windll.dwmapi.DwmSetWindowAttribute(ctypes.c_void_p(hwnd), attr, ctypes.byref(val), 4) == 0:
                    break
        except Exception:  # noqa: BLE001 - cosmetic only
            pass

    # ---------- settings ----------
    def save_selection(self, enabled: list[str], pdf_regulators: list[str]):
        s = self._s
        s.enabled, s.pdf_regulators, s.known_ids = enabled, pdf_regulators, list(self._by_id)
        s.save()
        return True

    def set_setting(self, key: str, value):
        s = self._s
        mapping = {"outputDir": "output_dir", "theme": "theme", "autoCheck": "auto_check", "autoTime": "auto_time",
                   "autoWeekday": "auto_weekday", "trayOnClose": "tray_on_close", "notify": "notify",
                   "checkUpdates": "check_updates", "days": "days"}
        if key == "startWithWindows":
            background.set_autostart(bool(value))
        elif key in mapping:
            setattr(s, mapping[key], value)
            if key == "autoCheck" and value != "off" and not s.last_auto_run:
                s.last_auto_run = datetime.now().isoformat(timespec="seconds")  # don't fire the moment it's enabled
            s.save()
        if key == "theme":
            self._titlebar()
        if s.auto_check != "off" or key == "startWithWindows" and value:
            self._ensure_tray()
        if self._tray:
            self._tray.refresh()
        return {"schedule": self._schedule_text()}

    def pick_folder(self):
        res = self._window.create_file_dialog(webview.FileDialog.FOLDER, directory=self._s.output_dir)
        if res:
            self.set_setting("outputDir", res[0])
            return res[0]
        return None

    # ---------- fetch ----------
    def fetch(self, since_iso: str, auto: bool = False):
        if self._running:
            return False
        ids = sorted(self._enabled())
        if not ids:
            self._emit("fetch:error", "No sources are switched on. Turn some on under Sources.")
            return False
        since = date.fromisoformat(since_iso)
        self._running, self._cancel = True, False
        total = len(ids)
        state = {"done": 0}

        def progress(msg: str):
            m = msg.strip()
            if m.startswith(("OK ", "FAIL ")):
                state["done"] += 1
                self._emit("fetch:progress", {"phase": "sources", "done": state["done"], "total": total, "line": m})
            elif m.startswith(("PDFs:", "Downloading PDFs", "Indexing", "Writing Excel")) or "PDFs" in m:
                self._emit("fetch:progress", {"phase": "finishing", "done": state["done"], "total": total, "line": m})

        def work():
            try:
                res = run(since, ids, self._s.pdf_regulators, self._out(), progress=progress,
                          cancel=lambda: self._cancel)
                payload = {"new": res.new_count, "total": res.total_in_period, "pdfs": res.pdfs_saved,
                           "failures": res.failures, "tracker": str(res.tracker),
                           "trackerCopy": res.tracker.name != TRACKER_NAME, "stopped": self._cancel,
                           "auto": auto, "summary": self._new_summary(res.new_count)}
                if auto:
                    self._s.last_auto_run = datetime.now().isoformat(timespec="seconds")
                    self._s.save()
                    if self._s.notify and res.new_count:
                        self._ensure_tray()
                        if self._tray:
                            self._tray.notify("Notified", payload["summary"])
                self._emit("fetch:done", payload)
            except Exception as e:  # noqa: BLE001
                log.exception("fetch failed")
                self._emit("fetch:error", f"{type(e).__name__}: {e}")
            finally:
                self._running = False

        threading.Thread(target=work, daemon=True).start()
        return True

    def cancel(self):
        self._cancel = True
        return True

    def _new_summary(self, n: int) -> str:
        if not n:
            return "Nothing new."
        try:
            from collections import Counter
            st = Store(self._out() / "history.db")
            try:
                _, _, ts = st.last_run()
                c = Counter(r["regulator"] for r in st.all_rows()
                            if r["first_seen"] and r["first_seen"].isoformat(timespec="seconds") == ts)
            finally:
                st.close()
            return f"{n} new update{'s' if n != 1 else ''}: " + ", ".join(f"{k} {v}" for k, v in c.most_common(4))
        except Exception:  # noqa: BLE001
            return f"{n} new updates"

    # ---------- register (results) ----------
    def results(self, q: str = "", regulator: str = "", topic: str = "", scope: str = "run", show_noise: bool = False):
        db = self._out() / "history.db"
        if not db.exists():
            return {"rows": [], "regulators": [], "topics": [], "hasHistory": False, "lastRunCount": 0}
        migrate_tracker(self._out())
        st = Store(db)
        try:
            rows = st.all_rows()
            last_uids, _, last_ts = st.last_run()
            from .usercols import merged
            user = merged(st, self._out() / TRACKER_NAME)
        finally:
            st.close()
        from .tagger import Tagger, ensure_rules
        Tagger(ensure_rules(self._out())).retag(rows)  # current rules, also for items fetched earlier
        try:
            from .dedupe import merge_duplicates
            rows = merge_duplicates(rows)
        except ImportError:
            pass
        regs = sorted({r["regulator"] for r in rows})
        topics = sorted({t for r in rows for t in r["tags"] if t != "Posted late"})

        def members(r):
            return r.get("member_uids") or [r["uid"]]

        if scope == "run" and last_uids:
            rows = [r for r in rows if any(u in last_uids for u in members(r))]
        if regulator:
            rows = [r for r in rows if r["regulator"] == regulator]
        if topic:
            rows = [r for r in rows if topic in r["tags"]]
        hidden = 0
        if not show_noise and not topic and not q:
            keep = [r for r in rows if not set(r["tags"]) & set(NOISE)]
            hidden, rows = len(rows) - len(keep), keep
        snippets: dict[str, str] = {}
        if q.strip():
            try:
                from .search import PdfIndex
                idx = PdfIndex(db)
                try:
                    for h in idx.search(q, limit=1000):
                        snippets[h["uid"]] = h.get("snippet", "") if h.get("in_pdf") else ""
                finally:
                    idx.close()
            except Exception:  # noqa: BLE001 - search index optional
                pass
            ql = q.lower()
            rows = [r for r in rows if any(u in snippets for u in members(r)) or ql in f"{r['title']} {r['ref_no']}".lower()]
        out = []
        for r in rows:
            u = next((user[m] for m in members(r) if user.get(m)), {})
            fs = r.get("first_seen")
            snip = next((snippets[m] for m in members(r) if snippets.get(m)), "")
            out.append({
                "uid": r["uid"], "date": r["date"].isoformat() if r["date"] else None, "regulator": r["regulator"],
                "type": r["doc_type"], "ref": r["ref_no"], "title": r["title"], "url": r["url"],
                "pdf": bool(r.get("local_pdf") and Path(r["local_pdf"]).exists()) or bool(r.get("pdf_url")),
                "tags": [t for t in r["tags"] if t not in ("Posted late",)], "late": "Posted late" in r["tags"],
                "new": bool(fs and last_ts and fs.isoformat(timespec="seconds") == last_ts),
                "status": u.get("Status", ""), "owner": u.get("Owner", ""), "notes": u.get("Notes", ""),
                "source": self._by_id[r["source_id"]].name if r["source_id"] in self._by_id else r["source_id"],
                "also": [{"regulator": a.get("regulator", ""), "source": self._by_id[a["source_id"]].name
                          if a.get("source_id") in self._by_id else a.get("source_id", ""),
                          "url": a.get("pdf_url") or a.get("url", "")} for a in r.get("also", [])],
                "snippet": snip,
            })
        out.sort(key=lambda r: (r["date"] or "", r["new"]), reverse=True)
        return {"rows": out, "regulators": regs, "topics": topics, "hasHistory": True, "hidden": hidden,
                "lastRunCount": len(last_uids)}

    def save_meta(self, uid: str, status: str, owner: str, notes: str):
        if status and status not in STATUS_CHOICES:
            status = ""
        st = Store(self._out() / "history.db")
        try:
            st.set_user_meta(uid, status=status, owner=owner.strip(), notes=notes.strip())
        finally:
            st.close()
        return True

    # ---------- open things ----------
    def open_url(self, url: str):
        if url and url.startswith(("http://", "https://")):
            webbrowser.open(url)
        return True

    def open_pdf(self, uid: str):
        st = Store(self._out() / "history.db")
        try:
            r = next((x for x in st.all_rows() if x["uid"] == uid), None)
        finally:
            st.close()
        if not r:
            return False
        if r.get("local_pdf") and Path(r["local_pdf"]).exists():
            os.startfile(r["local_pdf"])  # noqa: S606 - user's own downloaded file
        elif r.get("pdf_url"):
            webbrowser.open(r["pdf_url"])
        return True

    def open_tracker(self):
        p = self._out() / TRACKER_NAME
        if not p.exists():
            return False
        try:
            p = rewrite_tracker(self._out()) or p  # fold in Status/Notes edited in the app
        except Exception:  # noqa: BLE001
            pass
        os.startfile(str(p))  # noqa: S606
        return True

    def open_folder(self):
        self._out().mkdir(parents=True, exist_ok=True)
        os.startfile(str(self._out()))  # noqa: S606
        return True

    # ---------- digest ----------
    def make_digest(self, since_iso: str, until_iso: str, title: str, subtitle: str, regulators: list[str]):
        db = self._out() / "history.db"
        if not db.exists():
            return {"ok": False, "error": "Fetch updates first, then create a digest."}
        since, until = date.fromisoformat(since_iso), date.fromisoformat(until_iso)
        name = f"Notified Digest {since:%d %b} - {until:%d %b %Y}.pdf"
        res = self._window.create_file_dialog(webview.FileDialog.SAVE, directory=str(self._out()), save_filename=name,
                                             file_types=("PDF (*.pdf)",))
        if not res:
            return {"ok": False, "cancelled": True}
        path = Path(res if isinstance(res, str) else res[0])
        if path.suffix.lower() != ".pdf":
            path = path.with_suffix(".pdf")
        try:
            from .digest import build_digest
            from .tagger import ensure_rules
            from .usercols import merged
            st = Store(db)
            try:
                rows = st.all_rows()
                user_cols = merged(st, self._out() / TRACKER_NAME)
            finally:
                st.close()
            out = build_digest(rows, since, until, path, title=title or "Regulatory Update", subtitle=subtitle or "",
                               include_regulators=regulators or None, user_cols=user_cols,
                               rules_file=ensure_rules(self._out()))
            os.startfile(str(out))  # noqa: S606 - file we just wrote
            return {"ok": True, "path": str(out)}
        except Exception as e:  # noqa: BLE001
            log.exception("digest failed")
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    # ---------- app updates ----------
    def check_update(self):
        if not self._s.check_updates:
            return None
        self._release = updater.latest()
        r = self._release
        return {"version": r.version, "page": r.page, "canDownload": bool(r.exe_url)} if r else None

    def download_update(self):
        if not self._release:
            return {"ok": False}
        try:
            dest = updater.download(self._release, Path(os.environ.get("USERPROFILE", Path.home())) / "Downloads")
            subprocess.Popen(["explorer", "/select,", str(dest)])
            return {"ok": True, "path": str(dest)}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}

    # ---------- tray & schedule ----------
    def _schedule_text(self) -> str:
        s = self._s
        nd = background.next_due(s.auto_check, s.auto_time, s.auto_weekday,
                                 datetime.fromisoformat(s.last_auto_run) if s.last_auto_run else None)
        if not nd:
            return "Automatic checks are off"
        return f"Next check {nd:%a %d %b at %H:%M}" if nd > datetime.now() else "A check is due now"

    def _ensure_tray(self):
        if self._tray is None:
            try:
                self._tray = background.Tray(post=self._tray_action, status=self._schedule_text)
            except Exception:  # noqa: BLE001
                self._tray = None

    def _tray_action(self, action: str):
        if action == "show" and self._window:
            self._window.show()
            self._window.restore()
        elif action == "check":
            self._scheduled_fetch()
        elif action == "quit":
            self.quit()

    def _scheduled_fetch(self):
        s = self._s
        last = datetime.fromisoformat(s.last_auto_run) if s.last_auto_run else None
        since = (last.date() - timedelta(days=2)) if last else date.today() - timedelta(days=s.days if s.days > 0 else 7)
        if self.fetch(since.isoformat(), auto=True):
            self._emit("fetch:started", {"auto": True, "since": since.isoformat()})

    def scheduler_loop(self):
        while not self._quitting:
            try:
                s = self._s
                last = datetime.fromisoformat(s.last_auto_run) if s.last_auto_run else None
                nd = background.next_due(s.auto_check, s.auto_time, s.auto_weekday, last)
                if nd and nd <= datetime.now() and not self._running:
                    self._scheduled_fetch()
                if self._tray:
                    self._tray.refresh()
            except Exception:  # noqa: BLE001
                log.exception("scheduler tick")
            for _ in range(60):
                if self._quitting:
                    return
                threading.Event().wait(1)

    def on_closing(self):
        """Window X: hide to tray when background checks are on, else quit."""
        if self._quitting:
            return True
        s = self._s
        if s.tray_on_close or s.auto_check != "off":
            self._ensure_tray()
            if self._tray:
                self._window.hide()
                if not getattr(self, "_hinted", False):
                    self._tray.notify("Notified is still running", "It keeps checking on schedule. Right-click the tray icon to quit.")
                    self._hinted = True
                return False  # cancel the close
        self._quitting = True
        if self._tray:
            self._tray.stop()
        return True

    def quit(self):
        self._quitting = True
        if self._tray:
            self._tray.stop()
        if self._window:
            self._window.destroy()


def main(start_hidden: bool = False):
    try:  # own taskbar identity so Windows shows Notified's icon
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Notified.Desktop")
    except Exception:  # noqa: BLE001
        pass
    api = Api()
    bg = {"dark": "#121A21", "light": "#F5F6F2"}.get(api._s.theme, "#F5F6F2")
    win = webview.create_window("Notified", url=str(resource("web/index.html")), js_api=api, width=1240, height=820,
                                min_size=(980, 640), background_color=bg, hidden=start_hidden, text_select=True)
    api._window = win
    win.events.closing += api.on_closing
    win.events.shown += api._titlebar
    if start_hidden:
        api._ensure_tray()
    threading.Thread(target=api.scheduler_loop, daemon=True).start()
    webview.start(gui="edgechromium", icon=str(resource("assets/notified.ico")), private_mode=True)
