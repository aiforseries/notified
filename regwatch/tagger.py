"""Keyword-based topic tagging (offline, free). Rules come from tag_rules.json; users can edit a copy."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path

from .models import Item
from .paths import resource

# Regulator -> home topic, so every item gets at least one meaningful topic.
REGULATOR_DEFAULT = {
    "ICAI": "Audit & Assurance", "NFRA": "Audit & Assurance", "MCA": "Companies Act / LLP",
    "ICSI": "Companies Act / LLP", "CBDT": "Income Tax / TDS", "CBIC": "GST", "GSTN": "GST",
    "DGFT": "Customs & Foreign Trade", "SEBI": "SEBI / Listing", "RBI": "Banking / NBFC / FEMA",
    "MoLE": "Labour & Payroll", "EPFO": "Labour & Payroll", "ESIC": "Labour & Payroll",
    "MeitY": "Data Protection / IT", "CERT-In": "Data Protection / IT", "IBBI": "Insolvency & Valuation",
    "IFSCA": "IFSC / GIFT City", "IRDAI": "Insurance & Pension", "PFRDA": "Insurance & Pension",
    "CCI": "Competition Law",
}
# Tags that mean "not a professional update": they replace every other topic on the item.
NOISE_TAGS = ("Internal / Admin", "Enforcement news", "Students / Exams", "Events & CPE")
FLAG_TAGS = ("Deadline / Extension", "Posted late")
# Regulators whose posts span many subjects: their home topic applies only when nothing more specific matches.
WEAK_DEFAULT = {"ICAI", "ICSI"}
RULES_FILE = "tag_rules.json"
_SHIPPED_NOTE = ".tag_rules.shipped"  # hash of the rules version we last copied in


class Tagger:
    def __init__(self, rules_file: Path | None = None):
        path = rules_file if rules_file and rules_file.exists() else resource(RULES_FILE)
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        self.rules: list[tuple[str, re.Pattern]] = []
        for tag, words in raw.items():
            if tag.startswith("_") or not words:
                continue
            alt = "|".join(f"(?:{w})" for w in words)
            try:
                self.rules.append((tag, re.compile(rf"(?<![A-Za-z0-9])(?:{alt})(?![a-z])", re.I)))
            except re.error:  # a typo in a user's edited rule shouldn't stop tagging
                continue

    def tags_for(self, title: str, ref_no: str, doc_type: str, regulator: str) -> list[str]:
        text = f"{title} {ref_no} {doc_type}"
        tags = [t for t, rx in self.rules if rx.search(text)]
        if "Customs & Foreign Trade" in tags and regulator in ("CBDT", "GSTN"):
            tags.remove("Customs & Foreign Trade")
        d = REGULATOR_DEFAULT.get(regulator)
        if d and d not in tags:
            professional = [t for t in tags if t not in FLAG_TAGS and t not in NOISE_TAGS]
            if regulator in WEAK_DEFAULT and professional:
                pass  # e.g. an ICAI Direct Taxes Committee post belongs under Income Tax, not Audit
            else:
                tags.insert(0, d)
        for noise in NOISE_TAGS:  # housekeeping / enforcement news / student notices: not professional topics
            if noise in tags:
                return [noise] + (["Deadline / Extension"] if noise == "Students / Exams" and
                                  "Deadline / Extension" in tags else [])
        return tags

    def tag(self, item: Item) -> list[str]:
        return self.tags_for(item.title, item.ref_no, item.doc_type, item.regulator)

    def retag(self, rows: list[dict]) -> list[dict]:
        """Apply the current rules to stored rows (so improved rules also fix items fetched earlier)."""
        for r in rows:
            keep = [t for t in r.get("tags") or [] if t == "Posted late"]
            r["tags"] = self.tags_for(r.get("title", ""), r.get("ref_no", ""), r.get("doc_type", ""),
                                      r.get("regulator", "")) + keep
        return rows


def _digest(p: Path) -> str:
    return hashlib.sha256(p.read_bytes().replace(bytes([13, 10]), bytes([10]))).hexdigest()  # ignore CRLF/LF


def ensure_rules(output_dir: Path) -> Path:
    """The user's editable copy of the rules lives in the output folder. Copy it in on first use, and
    refresh it with improved shipped rules only if the user hasn't edited it since we last copied it."""
    output_dir.mkdir(parents=True, exist_ok=True)
    user, shipped, note = output_dir / RULES_FILE, resource(RULES_FILE), output_dir / _SHIPPED_NOTE
    if not user.exists():
        shutil.copy(shipped, user)
        note.write_text(_digest(user))
        return user
    if _digest(user) == _digest(shipped):
        note.write_text(_digest(user))
        return user
    last = note.read_text().strip() if note.exists() else ""
    if last and last == _digest(user):  # untouched since our last copy -> safe to update
        shutil.copy(shipped, user)
        note.write_text(_digest(user))
    elif not last and _is_older_shipped(user):  # pre-0.4 copy made before we kept a note
        shutil.copy(shipped, user)
        note.write_text(_digest(user))
    return user


# Hashes of rules files shipped before ensure_rules() existed, so those untouched copies can be upgraded too.
_OLD_SHIPPED = {
    "40c54fb324114672080822cb41c2e3c4d9cc127af755b660ed97775180d85b4d",
    "538d1202d1b9ed1717ee6bd52a38265830a190ceef688850bb6b112b8213dd4a",
}


def _is_older_shipped(p: Path) -> bool:
    return _digest(p) in _OLD_SHIPPED
