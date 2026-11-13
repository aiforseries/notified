"""Shared design tokens and small widgets for the RegWatch UI."""
from __future__ import annotations

import customtkinter as ctk

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
