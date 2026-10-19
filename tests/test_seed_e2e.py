"""端到端：13 則實際人工記錄的刊登（data/seed），結果要與 docs/research/poc-candidate-trials.md 一致。"""

from conftest import ROOT

from jev import identity
from jev.lifecycle import import_csv
from jev.report import listing_rows, top_candidates
from jev.db import unj


def test_seed_matches_document_trials(conn):
    import_csv(conn, ROOT / "data" / "seed" / "lianshang-listings-2026-10-18.csv", "INITIAL")
    rows = {r["external_id"]: r for r in listing_rows(conn)}
    assert all(r["membership"] == "MEMBER" for r in rows.values())

    def units(ext):
        f = unj(rows[ext]["resolved_fields"])["sales_unit"]
        return {f["value"]} if f["status"] == "NARROWED" else set(f.get("candidates", []))

    def tops(ext, n):
        return [(t["number"], t["floor"]) for t in top_candidates(conn, rows[ext]["address_assessment_id"], n)]

    assert units("2068MR") == {"D2", "D5"} and set(tops("2068MR", 2)) == {(103, 12), (107, 12)}
    assert tops("8749CJ", 1) == [(83, 12)]
    assert set(tops("20790495", 2)) == {(103, 9), (107, 9)}
    assert set(tops("20979644", 2)) == {(103, 4), (107, 4)}
    assert units("20981775") == {"C3"} and units("20461078") == {"C3"}
    assert units("20970583") == {"C1", "C2"}
    for ext in ("20703980", "20882109", "20936901", "20985583", "21043484"):
        assert units(ext) == {"D2", "D5"}
    assert sum(1 for r in rows.values() if r["address"] == "CONFIRMED") == 0  # 名冊未驗證：不會有系統確認
    matches = identity.evaluate_pairs(conn)
    assert all(m["outcome"] != "AUTO_MERGE_ELIGIBLE" for m in matches)
