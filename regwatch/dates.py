"""Lenient date parsing for the many formats Indian regulator sites use."""
from __future__ import annotations

import re
from datetime import date, datetime

_FORMATS = [
    "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y", "%Y-%m-%d", "%d-%b-%Y", "%d %b %Y", "%d %B %Y",
    "%b %d, %Y", "%B %d, %Y", "%d %b, %Y", "%d %B, %Y", "%d-%B-%Y", "%d/%m/%y", "%d-%m-%y",
    "%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z", "%a, %d %b %Y %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S",
]

_DATE_RX = re.compile(
    r"(\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}"
    r"|\d{4}-\d{2}-\d{2}"
    r"|\d{1,2}(?:st|nd|rd|th)?[\s-]+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*[\s,-]+\d{4}"
    r"|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4})",
    re.I,
)


def parse_date(text: str | None) -> date | None:
    if not text:
        return None
    s = " ".join(str(text).split()).strip()
    s = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", s, flags=re.I).replace("Sept", "Sep")
    for fmt in _FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    m = _DATE_RX.search(s)
    if m and m.group(0) != s:
        return parse_date(m.group(0))
    return None


def find_date(text: str | None) -> date | None:
    """Find the first date-looking substring in free text."""
    if not text:
        return None
    m = _DATE_RX.search(text)
    return parse_date(m.group(0)) if m else None
