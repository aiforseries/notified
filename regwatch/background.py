"""System-tray icon, scheduled checks, Windows notifications and start-with-Windows."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from typing import Callable

from .paths import resource

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "Notified"
LEGACY_RUN_NAME = "RegWatch"


# ---------- schedule ----------
def next_due(mode: str, at: str, weekday: int, last: datetime | None, now: datetime | None = None) -> datetime | None:
    """Next scheduled check. mode: off | daily | weekly. A missed slot (PC was off) is due immediately."""
    if mode not in ("daily", "weekly"):
        return None
    now = now or datetime.now()
    try:
        hh, mm = (int(x) for x in at.split(":"))
    except ValueError:
        hh, mm = 9, 0
    slot = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if mode == "weekly":
        slot -= timedelta(days=(slot.weekday() - weekday) % 7)
    else:
        if slot > now:
            slot -= timedelta(days=1)
    # `slot` = most recent scheduled time at or before now
    if slot > now:
        slot -= timedelta(days=7 if mode == "weekly" else 1)
    if last is None or last < slot:
        return slot  # overdue -> run now
    return slot + timedelta(days=7 if mode == "weekly" else 1)


def describe(mode: str, at: str, weekday: int) -> str:
    if mode == "daily":
        return f"Every day at {at}"
    if mode == "weekly":
        return f"Every {WEEKDAYS[weekday]} at {at}"
    return "Off"


# ---------- start with Windows ----------
def launch_command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --tray'
    pyw = sys.executable.replace("python.exe", "pythonw.exe")
    return f'"{pyw}" -m regwatch --tray'


def set_autostart(enabled: bool) -> None:
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        try:
            winreg.DeleteValue(k, LEGACY_RUN_NAME)  # entry written by RegWatch (pre-0.4)
        except FileNotFoundError:
            pass
        if enabled:
            winreg.SetValueEx(k, RUN_NAME, 0, winreg.REG_SZ, launch_command())
        else:
            try:
                winreg.DeleteValue(k, RUN_NAME)
            except FileNotFoundError:
                pass


def autostart_enabled() -> bool:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            for name in (RUN_NAME, LEGACY_RUN_NAME):
                try:
                    winreg.QueryValueEx(k, name)
                    return True
                except OSError:
                    continue
            return False
    except OSError:
        return False


# ---------- tray ----------
class Tray:
    """pystray icon running on its own thread. Menu actions are posted back via `post(action)`
    so all UI work happens on the Tk thread."""

    def __init__(self, post: Callable[[str], None], status: Callable[[], str]):
        import pystray
        from PIL import Image

        self._post = post
        img = Image.open(resource("assets/regwatch.png"))
        menu = pystray.Menu(
            pystray.MenuItem("Open Notified", lambda: post("show"), default=True),
            pystray.MenuItem("Check now", lambda: post("check")),
            pystray.MenuItem(lambda item: status(), None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", lambda: post("quit")),
        )
        self.icon = pystray.Icon("Notified", img, "Notified", menu)
        self.icon.run_detached()

    def notify(self, title: str, msg: str) -> None:
        try:
            self.icon.notify(msg, title)
        except Exception:  # noqa: BLE001 - notifications are best-effort
            pass

    def refresh(self) -> None:
        try:
            self.icon.update_menu()
        except Exception:  # noqa: BLE001
            pass

    def stop(self) -> None:
        try:
            self.icon.stop()
        except Exception:  # noqa: BLE001
            pass
