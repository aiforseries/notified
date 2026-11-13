"""Tests for regwatch.dedupe. Run: python -m pytest tests  (or: python tests/test_dedupe.py)"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from regwatch.dedupe import extract_refs, merge_duplicates, titles_match, token_set_ratio  # noqa: E402

_n = 0


def row(source_id, regulator, title, d, ref_no="", pdf_url="", tags=None, url=None):
    global _n
    _n += 1
    return dict(uid=f"u{_n}", source_id=source_id, regulator=regulator, title=title, date=d,
                url=url or f"https://example.org/{_n}", doc_type="", ref_no=ref_no, pdf_url=pdf_url,
                tags=tags or [], first_seen=None, local_pdf="")


def merged(rows):
    return [r for r in merge_duplicates(rows) if r.get("also")]


# ---------------------------------------------------------------- reference extraction

def test_extract_refs():
    assert extract_refs("Notification No. 45/2026-Central Tax") == {("notif", 45, 2026, "centraltax")}
    assert extract_refs("No. 05/2026- Central Tax (Rate)") == {("notif", 5, 2026, "centraltaxrate")}
    assert extract_refs("Notification No. 132/2026") == {("notif", 132, 2026, "")}
    assert extract_refs("G.S.R. 123(E)") == {("gsr", 123)}
    assert extract_refs("GSR 123 (E)") == {("gsr", 123)}
    assert extract_refs("S.O. 5458(E)") == {("so", 5458)}
    assert extract_refs("Circular No. 07/2026") == {("circ", "7", 2026)}
    assert extract_refs("Circular No. 256/02/2026-GST") == {("circ", "256/2", 2026)}
    assert extract_refs("RBI/2026-27/303 DoR.AML.REC.255/14.06.001/2026-27") == {("rbi", "", 2026, 27, 303)}
    assert extract_refs("RBI/DPSS/2026-27/475") == {("rbi", "DPSS", 2026, 27, 475)}
    assert extract_refs("so 12 people attended") == set()  # plain word 'so' is not S.O.


def test_title_refs_skip_referenced_documents():
    t = "Seeks to amend Notification No. 17/2017-Central Tax (Rate), dated 28th June, 2017"
    assert extract_refs(t, from_title=True) == set()
    t = "CBDT has notified the Income-tax (Eighth Amendment) Rules, 2026, vide Notification No. 139/2026"
    assert extract_refs(t, from_title=True) == {("notif", 139, 2026, "")}


# ---------------------------------------------------------------- similarity

def test_token_set_ratio():
    assert token_set_ratio("Master Direction on Note Sorting Machines", "master direction on note sorting machines") == 100
    assert token_set_ratio("abc", "") == 0
    assert token_set_ratio("Income tax rules", "GST council meeting") < 50


def test_titles_match_rules():
    assert titles_match("Reserve Bank of India (Commercial Banks – Fraud Risk Management) Directions, 2026",
                        "Reserve Bank of India (Commercial Banks - Fraud Risk Management) Directions, 2026")
    assert titles_match("MCA successfully concludes nationwide Special Campaign 6.0 on Swachhata",
                        "MCA successfully concludes nationwide Special Campaign 6.0 on Swachchta")  # spelling
    # one entity type swapped for another
    assert not titles_match("Reserve Bank of India (Payments Banks - Supervisory Returns) Directions, 2026",
                            "Reserve Bank of India (Small Finance Banks - Supervisory Returns) Directions, 2026")
    # an amendment is not the principal directions
    assert not titles_match("Reserve Bank of India (Domestic Money Transfer) Directions, 2026",
                            "Reserve Bank of India (Domestic Money Transfer) Amendment Directions, 2026")
    # different ordinals / numbers
    assert not titles_match("Income-tax (Twenty-fifth Amendment) Rules, 2026 notified by CBDT",
                            "Income-tax (Twenty-sixth Amendment) Rules, 2026 notified by CBDT")
    assert not titles_match("Companies (Accounts) Amendment Rules 2026 notified by the ministry today",
                            "Companies (Accounts) Amendment Rules 2025 notified by the ministry today")
    # too short to be sure
    assert not titles_match("Press release", "Press release")


# ---------------------------------------------------------------- merging: positives

def test_gst_notification_cbic_and_gazette():
    a = row("cbic_gst_notifications", "CBIC", "Seeks to notify the Central Goods and Services Tax (Amendment) Rules 2026.",
            date(2026, 10, 26), ref_no="Notification No. 05/2026-Central Tax", tags=["GST"])
    b = row("egazette_cbic", "CBIC", "Central Goods and Services Tax (Amendment) Rules, 2026",
            date(2026, 10, 24), ref_no="G.S.R. 950(E)", pdf_url="https://egazette/x.pdf",
            tags=["Rules"])
    b["title"] += " [Notification No. 05/2026-Central Tax]"
    out = merge_duplicates([a, b])
    assert len(out) == 1
    p = out[0]
    assert p["uid"] == a["uid"] and p["source_id"] == "cbic_gst_notifications"   # regulator's own listing wins
    assert p["tags"] == ["GST", "Rules"]
    assert p["also"][0]["source_id"] == "egazette_cbic" and p["also"][0]["pdf_url"] == "https://egazette/x.pdf"
    assert p["pdf_url"] == "https://egazette/x.pdf"     # filled from the member
    assert p["member_uids"] == [a["uid"], b["uid"]]


def test_cbdt_three_way_portal_gazette_pib():
    portal = row("cbdt_notifications", "CBDT", "CBDT has notified the Income-tax (Eighth Amendment) Rules, 2026, "
                 "vide Notification No. 139/2026 dated 30 October 2026", date(2026, 11, 3),
                 ref_no="Notification No. 139/2026", pdf_url="https://incometax/139.pdf")
    gazette = row("egazette_cbdt", "CBDT", "Income-tax (Eighth Amendment) Rules, 2026", date(2026, 10, 30),
                  ref_no="Notification No. 139/2026 G.S.R. 975(E)", pdf_url="https://egazette/975.pdf")
    pib = row("pib_cbdt", "CBDT", "CBDT notifies Income-tax (Eighth Amendment) Rules, 2026 vide Notification No. 139/2026",
              date(2026, 11, 4))
    out = merge_duplicates([pib, gazette, portal])
    assert len(out) == 1
    assert out[0]["uid"] == portal["uid"]
    assert sorted(a["source_id"] for a in out[0]["also"]) == ["egazette_cbdt", "pib_cbdt"]


def test_mca_gazette_and_pib_by_gsr_number():
    g = row("egazette_mca", "MCA", "The Companies (Listing of Equity Instruments in Permissible Jurisdictions) Amendment Rules 2026",
            date(2026, 11, 4), ref_no="G.S.R. 968(E)", pdf_url="https://egazette/968.pdf")
    p = row("pib_mca", "MCA", "MCA notifies amendment rules for listing of equity in permissible jurisdictions vide G.S.R. 968(E)",
            date(2026, 11, 6))
    out = merge_duplicates([p, g])
    assert len(out) == 1 and out[0]["source_id"] == "egazette_mca"   # eGazette outranks PIB


def test_rbi_notification_and_master_by_title():
    n = row("rbi_notifications", "RBI", "Reserve Bank of India (Domestic Money Transfer) Directions, 2026",
            date(2026, 11, 11), ref_no="RBI/DPSS/2026-27/476 CO.DPSS.POLC.No.S843/02-27-019/2026-2027",
            pdf_url="https://rbi/476.pdf")
    m = row("rbi_master", "RBI", "Reserve Bank of India (Domestic Money Transfer) Directions, 2026", date(2026, 11, 11))
    out = merge_duplicates([m, n])
    assert len(out) == 1 and out[0]["uid"] == n["uid"]   # the one with PDF + ref wins within a tier


def test_rbi_ref_in_other_title():
    n = row("rbi_notifications", "RBI", "Implementation of Section 51A of UAPA, 1967: Updates to UNSC's 1988 Taliban Sanctions List",
            date(2026, 10, 30), ref_no="RBI/2026-27/303 DoR.AML.REC.255/14.06.001/2026-27")
    m = row("rbi_master", "RBI", "UNSC sanctions list update (RBI/2026-27/303)", date(2026, 10, 31))
    assert len(merge_duplicates([n, m])) == 1


# ---------------------------------------------------------------- merging: negatives

def test_same_source_never_merges():
    # e-Gazette really publishes two S.O.s with identical titles on the same day
    a = row("egazette_cbdt", "CBDT", "Notification of District Legal Service Authority, Jind, Haryana for exemption",
            date(2026, 7, 21), ref_no="S.O. 3983(E)")
    b = row("egazette_cbdt", "CBDT", "Notification of District Legal Service Authority, Jind, Haryana for exemption",
            date(2026, 7, 21), ref_no="S.O. 3984(E)")
    assert merged([a, b]) == []
    c = row("cbic_gst_notifications", "CBIC", "Seeks to notify a special procedure under Section 148 for payment of tax",
            date(2026, 10, 28), ref_no="Notification No. 10/2026-Central Tax (Rate)")
    d = row("cbic_gst_notifications", "CBIC", "Seeks to notify a special procedure under Section 148 for payment of tax",
            date(2026, 10, 28), ref_no="Notification No. 10/2026-Integrated Tax (Rate)")
    assert merged([c, d]) == []


def test_conflicting_own_refs_block_title_match():
    a = row("egazette_cbdt", "CBDT", "Notification in the case of Kerala Headload Workers Welfare Board, Kochi",
            date(2026, 7, 23), ref_no="S.O. 4048(E)")
    b = row("pib_cbdt", "CBDT", "Notification in the case of Kerala Headload Workers Welfare Board, Kochi",
            date(2026, 7, 23), ref_no="S.O. 4047(E)")
    assert merged([a, b]) == []


def test_series_differs():
    a = row("cbic_gst_notifications", "CBIC", "Seeks to amend rate notification", date(2026, 10, 28),
            ref_no="Notification No. 09/2026-Central Tax (Rate)")
    b = row("egazette_cbic", "CBIC", "Rate amendment", date(2026, 10, 28),
            ref_no="Notification No. 09/2026-Integrated Tax (Rate)")
    assert merged([a, b]) == []


def test_amended_notification_number_in_title_is_not_a_match():
    old = row("egazette_cbic", "CBIC", "Notification No. 17/2017-Central Tax (Rate)", date(2026, 10, 25),
              ref_no="Notification No. 17/2017-Central Tax (Rate)")
    new = row("cbic_gst_notifications", "CBIC", "Seeks to amend Notification No. 17/2017-Central Tax (Rate), dated 28th June, 2017",
              date(2026, 10, 28), ref_no="Notification No. 09/2026-Central Tax (Rate)")
    assert merged([old, new]) == []


def test_same_number_different_regulator():
    cbdt = row("cbdt_notifications", "CBDT", "CBDT notifies something", date(2026, 10, 2), ref_no="Notification No. 45/2026")
    sebi = row("sebi_circulars", "SEBI", "SEBI circular", date(2026, 10, 2), ref_no="Notification No. 45/2026")
    assert merged([cbdt, sebi]) == []
    c1 = row("cbdt_circulars", "CBDT", "Due date extension", date(2026, 9, 29), ref_no="Circular No. 07/2026")
    c2 = row("rbi_notifications", "RBI", "Something else", date(2026, 9, 29), ref_no="Circular No. 07/2026")
    assert merged([c1, c2]) == []


def test_dates_too_far_apart():
    a = row("egazette_mca", "MCA", "Companies (Accounts) Amendment Rules 2026", date(2026, 9, 1), ref_no="G.S.R. 500(E)")
    b = row("pib_mca", "MCA", "MCA notifies G.S.R. 500(E)", date(2026, 9, 20))
    assert merged([a, b]) == []
    t1 = row("rbi_master", "RBI", "Master Direction on Note Sorting Machines and related matters", date(2026, 10, 2))
    t2 = row("rbi_notifications", "RBI", "Master Direction on Note Sorting Machines and related matters", date(2026, 10, 9))
    assert merged([t1, t2]) == []      # title match only within +-5 days
    t3 = row("rbi_notifications", "RBI", "Master Direction on Note Sorting Machines and related matters", None)
    assert merged([t1, t3]) == []      # undated never merges


def test_title_match_needs_compatible_regulator():
    a = row("pib_mca", "MCA", "Special Campaign 6.0 concludes successfully across all field offices", date(2026, 10, 31))
    b = row("pib_cbdt", "CBDT", "Special Campaign 6.0 concludes successfully across all field offices", date(2026, 10, 31))
    assert merged([a, b]) == []
    c = row("pib_finance", "MoF", "Special Campaign 6.0 concludes successfully across all field offices", date(2026, 10, 31))
    assert len(merged([b, c])) == 1     # MoF press release ~ CBDT press release


def test_chain_does_not_join_conflicting_documents():
    # B (no own ref) looks like both A and C, but A and C are different S.O.s from different sources
    a = row("egazette_cbdt", "CBDT", "Exemption in the case of Maharashtra Electricity Regulatory Commission Mumbai",
            date(2026, 8, 25), ref_no="S.O. 4706(E)")
    b = row("pib_cbdt", "CBDT", "Exemption in the case of Maharashtra Electricity Regulatory Commission Mumbai",
            date(2026, 8, 25))
    c = row("cbdt_notifications", "CBDT", "Exemption in the case of Maharashtra Electricity Regulatory Commission Mumbai",
            date(2026, 8, 25), ref_no="S.O. 4707(E)")
    out = merge_duplicates([a, b, c])
    assert len(out) == 2
    assert all(len(r.get("member_uids", [r["uid"]])) <= 2 for r in out)


def test_inputs_untouched_and_order_kept():
    a = row("rbi_master", "RBI", "Reserve Bank of India (Commercial Banks – Credit Valuation Adjustment Framework) Directions, 2026",
            date(2026, 10, 7))
    x = row("sebi_circulars", "SEBI", "Unrelated SEBI circular on mutual fund disclosures", date(2026, 10, 7))
    b = row("rbi_notifications", "RBI", "Reserve Bank of India (Commercial Banks - Credit Valuation Adjustment Framework) Directions, 2026",
            date(2026, 10, 7), ref_no="RBI/DOR/2026-27/474")
    out = merge_duplicates([a, x, b])
    assert [r["uid"] for r in out] == [b["uid"], x["uid"]]
    assert "also" not in a and "also" not in b


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print("ok ", t.__name__)
    print(f"{len(tests)} passed")
