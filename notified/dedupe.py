"""Collapse the same real-world document reported by several sources (regulator listing, eGazette, PIB ...).

Matching is deliberately conservative - a wrong merge hides a document, a missed merge only shows it twice:
  * reference numbers (Notification No. 45/2026-Central Tax, G.S.R. 123(E), S.O. 5458(E), Circular No. 7/2026,
    RBI/2026-27/303, SEBI/HO/...) read from ref_no or the title, dates within +-10 days. At least one side
    must carry the number in its own ref_no field, and numbers the title only *refers to* ("amends
    Notification No. 12/2017") are ignored;
  * otherwise a near-identical title (token-set ratio, same numbers and ordinals) within +-5 days and a
    compatible regulator.
Two rows from the same source are never merged, nor rows whose own ref numbers of the same kind disagree.
"""
from __future__ import annotations

import re
from datetime import date
from difflib import SequenceMatcher

REF_DAYS = 10
TITLE_DAYS = 5
TITLE_MIN_RATIO = 92.0
TITLE_MIN_COMMON = 6       # shared distinct non-filler tokens
TITLE_MIN_COMMON_EXACT = 4  # ... when both titles have exactly the same words
TITLE_MIN_COVERAGE = 0.8   # shared tokens / tokens of the longer title

_SERIES = (r"central\s+tax(?:\s*\(\s*rate\s*\))?|integrated\s+tax(?:\s*\(\s*rate\s*\))?"
           r"|union\s+territory\s+tax(?:\s*\(\s*rate\s*\))?|compensation\s+cess(?:\s*\(\s*rate\s*\))?"
           r"|customs(?:\s*\(\s*(?:n\.?\s*t\.?|add|cvd|sg|ss)\s*\))?")
_PATTERNS = [
    # Notification No. 45/2026-Central Tax  /  No. 45/2026-Customs (N.T.)  /  45/2026-Central Tax (Rate)
    ("notif", re.compile(r"(?:notification\s*)?(?:no\.?\s*)?\b(\d{1,4})\s*/\s*(\d{4})\s*[-–—]\s*(" + _SERIES + r")", re.I)),
    # Notification No. 132/2026 (CBDT style, no series)
    ("notif", re.compile(r"notification\s*(?:no\.?|number)?\s*[:.]?\s*(\d{1,4})\s*/\s*(\d{4})\b(?!\s*[-–—]\s*[a-z])", re.I)),
    ("gsr", re.compile(r"\bg\.?\s*s\.?\s*r\.?\s*(\d{1,5})\s*(\(\s*e\s*\))?", re.I)),
    ("so", re.compile(r"\bs\.\s*o\.?\s*(\d{1,5})\s*\(\s*e\s*\)|\bso\s*(\d{1,5})\s*\(\s*e\s*\)", re.I)),
    # Circular No. 07/2026  /  Circular No. 250/07/2026-GST
    ("circ", re.compile(r"circular\.?\s*(?:no\.?|number)?\s*[:.]?\s*((?:\d{1,4}\s*/\s*)*\d{1,4})\s*/\s*(\d{4})\b", re.I)),
    # RBI/2026-27/303, RBI/FED/2026-27/298, RBI/DPSS/2026-27/475 (department series kept in the key)
    ("rbi", re.compile(r"\brbi\s*/\s*(?:([a-z]{2,6})\s*/\s*)?(\d{4})\s*-\s*(\d{2,4})\s*/\s*(\d{1,4})\b", re.I)),
    ("sebi", re.compile(r"\bsebi\s*/\s*ho\s*/[a-z0-9_\-/ ]*?/\s*(\d{4})\s*/\s*(\d{1,5})\b", re.I)),
]
# A number in a title right after one of these words is a *referenced* document, not the item itself.
_REFERS = re.compile(r"\b(amend|amends|amending|amended|amendments?\s+(?:to|of|in)|supersed\w*|rescind\w*|corrigend\w*|"
                     r"errat\w*|modif\w*|withdraw\w*|in\s+terms\s+of|under|extension\s+of|extends?|earlier|previous|"
                     r"clarification|clarifies|read\s+with|r\.?w\.?)\b[^.;]{0,30}$", re.I)
_ORDINALS = {"first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth", "tenth",
             "eleventh", "twelfth", "thirteenth", "fourteenth", "fifteenth", "sixteenth", "seventeenth",
             "eighteenth", "nineteenth", "twentieth", "thirtieth", "fortieth", "fiftieth"}
_TAX_FAMILY = {"MoF", "CBDT", "CBIC"}


def _norm_num(s: str) -> str:
    return "/".join(str(int(p)) for p in re.split(r"\s*/\s*", s.strip()) if p)


def _key(kind: str, m: re.Match) -> tuple:
    g = m.groups()
    if kind == "notif":
        series = re.sub(r"[^a-z]", "", (g[2] if len(g) > 2 and g[2] else "").lower())
        return ("notif", int(g[0]), int(g[1]), series)
    if kind == "gsr":
        return ("gsr", int(g[0]))
    if kind == "so":
        return ("so", int(g[0] or g[1]))
    if kind == "circ":
        return ("circ", _norm_num(g[0]), int(g[1]))
    if kind == "rbi":
        return ("rbi", (g[0] or "").upper(), int(g[1]), int(g[2][-2:]), int(g[3]))
    if kind == "sebi":
        return ("sebi", int(g[0]), int(g[1]))
    raise ValueError(kind)


def extract_refs(text: str, from_title: bool = False) -> set[tuple]:
    """Normalised reference keys found in text. With from_title, numbers that are only referred to are skipped."""
    out, taken = set(), []
    for kind, rx in _PATTERNS:
        for m in rx.finditer(text or ""):
            if any(m.start() < e and s < m.end() for s, e in taken):
                continue  # already matched by a more specific pattern
            if from_title and _REFERS.search(text[:m.start()]):
                continue
            taken.append((m.start(), m.end()))
            out.add(_key(kind, m))
    return out


def _keys_equal(a: tuple, b: tuple) -> bool:
    if a[0] != b[0]:
        return False
    if a[0] == "notif":  # a missing series ("Notification No. 45/2026") matches any series of the same number
        return a[1:3] == b[1:3] and (a[3] == b[3] or not a[3] or not b[3])
    return a == b


def _regs_compatible(a: str, b: str) -> bool:
    return a == b or (("MoF" in (a, b)) and a in _TAX_FAMILY and b in _TAX_FAMILY)


# ---------------------------------------------------------------- title similarity

def _tokens(s: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (s or "").lower())


def token_set_ratio(a: str, b: str) -> float:
    """0-100, like fuzzywuzzy's token_set_ratio: compares the shared tokens against each side's full set."""
    ta, tb = set(_tokens(a)), set(_tokens(b))
    if not ta or not tb:
        return 0.0
    inter = " ".join(sorted(ta & tb))
    sa = (inter + " " + " ".join(sorted(ta - tb))).strip()
    sb = (inter + " " + " ".join(sorted(tb - ta))).strip()

    def r(x, y):
        return SequenceMatcher(None, x, y).ratio() if x and y else 0.0
    return 100.0 * max(r(inter, sa), r(inter, sb), r(sa, sb))


_NOISE = {"the", "of", "and", "a", "an", "for", "in", "on", "to", "by", "with", "at", "from", "under", "is", "are",
          "s", "reg", "regarding", "re", "amp", "nbsp", "br", "notification", "press", "release", "pib", "issued",
          "india", "government", "govt", "ministry", "gazette", "e", "dated"}


def _spelling_variant(x: str, y: str) -> bool:
    return (len(x) >= 5 and len(y) >= 5 and not any(c.isdigit() for c in x + y)
            and SequenceMatcher(None, x, y).ratio() >= 0.85)


def titles_match(a: str, b: str) -> bool:
    """Near-identical titles only. Every word that is not in both titles must be filler or a spelling variant
    of a word in the other title: 'Payments Banks - Supervisory Returns' never matches 'Small Finance Banks -
    Supervisory Returns', nor 'X Directions' 'X Amendment Directions'."""
    ta, tb = set(_tokens(a)), set(_tokens(b))
    common = ta & tb
    if ta == tb and len(ta - _NOISE) >= TITLE_MIN_COMMON_EXACT:
        return True  # identical wording ("Master Direction on Note Sorting Machines")
    if len(common - _NOISE) < TITLE_MIN_COMMON or len(common) / max(len(ta), len(tb)) < TITLE_MIN_COVERAGE:
        return False
    nums_a, nums_b = {t for t in ta if any(c.isdigit() for c in t)}, {t for t in tb if any(c.isdigit() for c in t)}
    if nums_a != nums_b or (ta & _ORDINALS) != (tb & _ORDINALS):
        return False  # "(25th Amendment) Rules" vs "(26th Amendment) Rules", "No. 45" vs "No. 46"
    left_a, left_b = sorted(ta - tb - _NOISE), sorted(tb - ta - _NOISE)
    if len(left_a) != len(left_b):
        return False
    for x in left_a:
        y = next((y for y in left_b if _spelling_variant(x, y)), None)
        if y is None:
            return False
        left_b.remove(y)
    return token_set_ratio(a, b) >= TITLE_MIN_RATIO


# ---------------------------------------------------------------- merging

def _tier(source_id: str) -> int:
    if source_id.startswith("pib_"):
        return 2
    if source_id.startswith("egazette_") or source_id.endswith("_gazette"):
        return 1
    return 0  # the regulator's own listing


def _rank(r: dict) -> tuple:
    return (_tier(r.get("source_id", "")), not (r.get("local_pdf") or r.get("pdf_url")), not r.get("ref_no"),
            str(r.get("first_seen") or ""), r.get("source_id", ""), r.get("uid", ""))


def _days(a: date | None, b: date | None) -> int | None:
    return abs((a - b).days) if a and b else None


class _Info:
    __slots__ = ("row", "own", "title_refs")

    def __init__(self, row: dict):
        self.row = row
        self.own = extract_refs(row.get("ref_no") or "")
        self.title_refs = extract_refs(row.get("title") or "", from_title=True)


def _conflict(a: _Info, b: _Info) -> bool:
    """Same source, or each carries its own number of the same kind and they differ."""
    if a.row.get("source_id") == b.row.get("source_id"):
        return True
    for ka in a.own:
        same_kind = [kb for kb in b.own if kb[0] == ka[0]]
        if same_kind and not any(_keys_equal(x, y) for x in a.own if x[0] == ka[0] for y in same_kind):
            return True
    return False


def _ref_match(a: _Info, b: _Info) -> bool:
    d = _days(a.row.get("date"), b.row.get("date"))
    if d is None or d > REF_DAYS:
        return False
    for mine, theirs in ((a.own, b.own | b.title_refs), (b.own, a.own | a.title_refs)):
        for x in mine:
            for y in theirs:
                if _keys_equal(x, y):
                    if x[0] in ("notif", "circ") and not _regs_compatible(a.row.get("regulator", ""), b.row.get("regulator", "")):
                        continue  # these numbers restart per regulator
                    return True
    return False


def _title_match(a: _Info, b: _Info) -> bool:
    d = _days(a.row.get("date"), b.row.get("date"))
    if d is None or d > TITLE_DAYS:
        return False
    if not _regs_compatible(a.row.get("regulator", ""), b.row.get("regulator", "")):
        return False
    return titles_match(a.row.get("title", ""), b.row.get("title", ""))


def merge_duplicates(rows: list[dict]) -> list[dict]:
    """One row per real-world document. The primary (most authoritative) row keeps its uid and gets
    row['also'] = [{source_id, regulator, url, pdf_url, ref_no, uid}, ...] for the others, row['member_uids']
    (primary first), the union of tags, and any ref_no / pdf_url / local_pdf it lacked. Inputs are not modified."""
    infos = [_Info(r) for r in rows]
    n = len(infos)
    # Candidate pairs: only rows within the widest window can match, so sort by date and sweep.
    order = sorted((i for i in range(n) if infos[i].row.get("date")), key=lambda i: infos[i].row["date"])
    pairs = []
    for x, i in enumerate(order):
        di = infos[i].row["date"]
        for j in order[x + 1:]:
            if (infos[j].row["date"] - di).days > REF_DAYS:
                break
            a, b = infos[i], infos[j]
            if _conflict(a, b):
                continue
            if _ref_match(a, b):
                pairs.append((0, 0.0, i, j))
            elif _title_match(a, b):
                pairs.append((1, -token_set_ratio(a.row["title"], b.row["title"]), i, j))
    pairs.sort()

    group = list(range(n))
    members = {i: [i] for i in range(n)}
    for _, _, i, j in pairs:
        gi, gj = group[i], group[j]
        if gi == gj:
            continue
        # Merge whole groups only if no member pair conflicts (prevents chains A~B~C where A and C differ).
        if any(_conflict(infos[p], infos[q]) for p in members[gi] for q in members[gj]):
            continue
        for k in members[gj]:
            group[k] = gi
        members[gi].extend(members.pop(gj))

    out = []
    for ms in sorted(members.values(), key=min):  # keep the input order (position of the first member)
        if len(ms) == 1:
            out.append(dict(rows[ms[0]]))
            continue
        ranked = sorted((rows[k] for k in ms), key=_rank)
        prim = dict(ranked[0])
        tags = list(prim.get("tags") or [])
        for r in ranked[1:]:
            for t in r.get("tags") or []:
                if t not in tags:
                    tags.append(t)
            for f in ("ref_no", "pdf_url", "local_pdf"):
                if not prim.get(f) and r.get(f):
                    prim[f] = r[f]
        prim["tags"] = tags
        prim["also"] = [{k: r.get(k, "") for k in ("source_id", "regulator", "url", "pdf_url", "ref_no", "uid")}
                        for r in ranked[1:]]
        prim["member_uids"] = [r["uid"] for r in ranked]
        out.append(prim)
    return out
