"""Keyword-based topic tagging (offline, free). Rules come from tag_rules.json; users can edit a copy."""
from __future__ import annotations

import json
import re
from pathlib import Path

from .models import Item
from .paths import resource

# Regulator -> default tag, so every item gets at least one meaningful topic.
REGULATOR_DEFAULT = {
    "ICAI": "Audit & Assurance", "NFRA": "Audit & Assurance", "MCA": "Companies Act / LLP",
    "CBDT": "Income Tax / TDS", "CBIC": "GST", "GSTN": "GST", "SEBI": "SEBI / Listing",
    "RBI": "Banking / NBFC / FEMA", "MoLE": "Labour & Payroll", "EPFO": "Labour & Payroll",
    "ESIC": "Labour & Payroll", "MeitY": "Data Protection / IT", "CERT-In": "Data Protection / IT",
}


class Tagger:
    def __init__(self, rules_file: Path | None = None):
        path = rules_file if rules_file and rules_file.exists() else resource("tag_rules.json")
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        self.rules: list[tuple[str, re.Pattern]] = []
        for tag, words in raw.items():
            if tag.startswith("_") or not words:
                continue
            alt = "|".join(f"(?:{w})" for w in words)
            self.rules.append((tag, re.compile(rf"(?<![A-Za-z0-9])(?:{alt})(?![a-z])", re.I)))

    def tag(self, item: Item) -> list[str]:
        text = f"{item.title} {item.ref_no} {item.doc_type}"
        tags = [t for t, rx in self.rules if rx.search(text)]
        if "Customs & Foreign Trade" in tags and item.regulator in ("CBDT", "GSTN"):
            tags.remove("Customs & Foreign Trade")
        d = REGULATOR_DEFAULT.get(item.regulator)
        if d and d not in tags:
            tags.insert(0, d)
        if "Internal / Admin" in tags:  # regulator housekeeping: keep it out of professional topics
            tags = ["Internal / Admin"]
        if "Students / Exams" in tags:  # student/exam noise shouldn't also look like a professional update
            tags = [t for t in tags if t in ("Students / Exams", "Deadline / Extension")]
        return tags
