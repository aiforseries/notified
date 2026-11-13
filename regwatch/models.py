from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date


@dataclass
class Item:
    """One regulatory update (notification / circular / announcement / press release)."""
    source_id: str          # e.g. "rbi_notifications" - matches Source.id
    regulator: str          # e.g. "RBI"
    title: str
    date: date | None       # date of issue as shown by the regulator
    url: str                # page or document link (absolute)
    doc_type: str = ""      # "Notification", "Circular", "Press Release", "Announcement", ...
    ref_no: str = ""        # e.g. "G.S.R. 123(E)", "Circular No. 5/2026"
    pdf_url: str = ""       # direct PDF link if known (absolute)
    tags: list[str] = field(default_factory=list)  # filled by tagger

    @property
    def uid(self) -> str:
        key = f"{self.source_id}|{self.pdf_url or self.url}|{self.title.strip().lower()}"
        return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
