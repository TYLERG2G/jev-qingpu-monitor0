"""候選報告：每則刊登的社區歸屬、戶別與完整門牌候選（給人看的 Markdown）。"""

from .db import unj


def listing_rows(conn):
    return conn.execute(
        "SELECT l.id, l.external_id, l.url, l.status, s.code AS source_code, sn.building_ping, sn.parking_ping, sn.floor_text,"
        " sn.rooms, sn.total_price, m.outcome AS membership, m.rule_score, a.outcome AS address, a.resolved_fields, a.flags,"
        " a.candidate_count, a.id AS address_assessment_id, l.property_id"
        " FROM listing l JOIN source s ON s.id=l.source_id"
        " JOIN listing_snapshot sn ON sn.id=(SELECT MAX(id) FROM listing_snapshot WHERE listing_id=l.id)"
        " JOIN assessment m ON m.listing_id=l.id AND m.subject_type='COMMUNITY_MEMBERSHIP' AND m.status='CURRENT'"
        " JOIN assessment a ON a.listing_id=l.id AND a.subject_type='ADDRESS_RESOLUTION' AND a.status='CURRENT'"
        " ORDER BY l.id").fetchall()


def top_candidates(conn, assessment_id, limit=6):
    return conn.execute(
        "SELECT su.code, CASE WHEN rm.record_status='RETRACTED' THEN '?' ELSE au.number END AS number, d.floor, ac.rule_score"
        " FROM assessment_candidate ac JOIN candidate c ON c.id=ac.candidate_id"
        " JOIN dwelling d ON d.id=c.dwelling_id JOIN sales_unit su ON su.id=d.sales_unit_id"
        " JOIN registry_mapping rm ON rm.from_id=su.id AND rm.mapping_type='SALES_UNIT_TO_NUMBER' JOIN address_unit au ON au.id=rm.to_id"
        " WHERE ac.assessment_id=? AND ac.excluded=0 ORDER BY ac.rule_score DESC, su.code, d.floor LIMIT ?",
        (assessment_id, limit)).fetchall()


def _field(fields, key):
    f = fields.get(key, {})
    if f.get("status") in ("NARROWED", "CONFIRMED"):
        return f"{f['value']}（{f['status']}）"
    if f.get("status") == "AMBIGUOUS":
        vals = f.get("candidates", [])
        return "／".join(map(str, vals[:6])) + ("…" if len(vals) > 6 else "") + "（AMBIGUOUS）"
    return "—"


def markdown(conn):
    lines = ["# JEV 候選報告", "",
             "> 規則分數不是機率；完整門牌在名冊與推導規則驗證前都只是**候選**。",
             "",
             "| # | 來源／編號 | 坪數 | 樓層 | 社區 | 戶別 | 樓層判定 | 門牌狀態 | 前幾名候選 |",
             "|---|------------|------|------|------|------|----------|----------|------------|"]
    for r in listing_rows(conn):
        fields = unj(r["resolved_fields"], {})
        tops = top_candidates(conn, r["address_assessment_id"], 4)
        top = "、".join(f"{t['number']}號{t['floor']}樓({t['rule_score']:.2f})" for t in tops) if r["address"] != "INSUFFICIENT_INFO" else "（候選超過 40 個，只顯示戶別）"
        lines.append(f"| {r['id']} | {r['source_code']} {r['external_id']} | {r['building_ping']} | {r['floor_text'] or '—'} | "
                     f"{r['membership']} | {_field(fields, 'sales_unit')} | {_field(fields, 'floor')} | "
                     f"{r['address']}（{r['candidate_count']}） | {top} |")
    lines += ["", "## 同屋配對候選", "", "| 配對 | 分數 | 結果 | 衝突 |", "|------|------|------|------|"]
    for m in conn.execute(
            "SELECT im.*, a.outcome, a.rule_score FROM identity_match im JOIN assessment a ON a.id=im.current_assessment_id"
            " ORDER BY a.rule_score DESC, im.id"):
        lines.append(f"| {m['listing_a_id']} ↔ {m['listing_b_id']} | {m['rule_score']:.2f} | {m['outcome']} | {', '.join(unj(m['conflicts'], [])) or '—'} |")
    return "\n".join(lines) + "\n"
