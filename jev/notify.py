"""通知（docs/12 §5、ADR-007）。

通知管道尚未決定（Q-005），MVP 先寫入本機 outbox（JSON 檔），狀態維持 PENDING 並註記 DRY_RUN；
設定 SMTP 之後才會真的寄出。只有 notify_eligible = true 的事件會比對規則；更正通知另外處理。
"""

import json
import os
import smtplib
from email.message import EmailMessage
from pathlib import Path

from .db import j, now, unj

DEFAULT_RULES = [
    ("新刊登與同屋新刊登", ["LISTING_NEW_CONFIRMED", "DWELLING_NEW_LISTING", "LISTING_RELISTED"]),
    ("降價與下架", ["LISTING_PRICE_CHANGED", "LISTING_DELISTED"]),
]


def ensure_default_rules(conn):
    if conn.execute("SELECT 1 FROM alert_rule").fetchone():
        return
    for name, types in DEFAULT_RULES:
        conn.execute("INSERT INTO alert_rule (name, event_types, channel) VALUES (?,?, 'EMAIL')", (name, j(types)))
    conn.commit()


def _render(conn, ev):
    payload = unj(ev["payload"])
    lst = conn.execute("SELECT l.*, s.code AS source_code FROM listing l JOIN source s ON s.id=l.source_id WHERE l.id=?",
                       (ev["listing_id"],)).fetchone() if ev["listing_id"] else None
    addr = conn.execute("SELECT resolved_fields, outcome FROM assessment WHERE subject_type='ADDRESS_RESOLUTION'"
                        " AND listing_id=? AND status='CURRENT'", (ev["listing_id"],)).fetchone() if ev["listing_id"] else None
    fields = unj(addr["resolved_fields"]) if addr and addr["resolved_fields"] else {}
    unit = fields.get("sales_unit", {})
    unit_text = unit.get("value") or "／".join(map(str, unit.get("candidates", []) or [])) or "未知"
    title = {"LISTING_NEW_CONFIRMED": "新刊登", "DWELLING_NEW_LISTING": "同一住宅新刊登", "LISTING_RELISTED": "重新上架",
             "LISTING_PRICE_CHANGED": "價格變動", "LISTING_DELISTED": "下架"}.get(ev["event_type"], ev["event_type"])
    lines = [f"[JEV] {title}"]
    if ev["event_type"] == "LISTING_PRICE_CHANGED":
        lines.append(f"{payload['old_price']:,} → {payload['new_price']:,} 元（{payload['pct']:+.1%}）")
    lines.append(f"戶別候選：{unit_text}；門牌狀態：{addr['outcome'] if addr else '—'}（候選，非確認）")
    if lst:
        lines.append(f"來源：{lst['source_code']} {lst['url'] or lst['external_id']}")
    return {"subject": lines[0], "text": "\n".join(lines), "event_type": ev["event_type"], "event_id": ev["event_id"]}


def _deliver(rule, rendered, outbox):
    host = os.environ.get("JEV_SMTP_HOST")
    if rule["channel"] == "EMAIL" and host:
        msg = EmailMessage()
        msg["Subject"], msg["From"], msg["To"] = rendered["subject"], os.environ["JEV_SMTP_FROM"], os.environ["JEV_SMTP_TO"]
        msg.set_content(rendered["text"])
        try:
            with smtplib.SMTP(host, int(os.environ.get("JEV_SMTP_PORT", "587")), timeout=30) as s:
                s.starttls()
                s.login(os.environ["JEV_SMTP_USER"], os.environ["JEV_SMTP_PASSWORD"])
                s.send_message(msg)
        except (OSError, smtplib.SMTPException) as e:
            return "FAILED", f"{type(e).__name__}: {e}"[:200]
        return "SENT", None
    token, chat = os.environ.get("JEV_TELEGRAM_BOT_TOKEN"), os.environ.get("JEV_TELEGRAM_CHAT_ID")
    if rule["channel"] == "TELEGRAM" and token and chat:
        import urllib.parse
        import urllib.request
        data = urllib.parse.urlencode({"chat_id": chat, "text": rendered["text"][:4000], "disable_web_page_preview": "true"}).encode()
        try:
            with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data=data, timeout=20) as r:
                ok = json.loads(r.read().decode("utf-8")).get("ok")
        except OSError as e:
            return "FAILED", f"{type(e).__name__}: {e}"[:200].replace(token, "***")
        return ("SENT", None) if ok else ("FAILED", "Telegram 回應 ok=false")
    Path(outbox).mkdir(parents=True, exist_ok=True)
    (Path(outbox) / f"{rendered['event_id']}_{rule['id']}.json").write_text(json.dumps(rendered, ensure_ascii=False, indent=2), encoding="utf-8")
    return "PENDING", "DRY_RUN_OUTBOX（Q-005 尚未決定通知管道）"


def _matches(conn, rule, ev):
    """規則門檻（12 §5）：事件類型、社區歸屬、總價上限、降幅下限、房數下限、坪數下限。"""
    if ev["event_type"] not in unj(rule["event_types"]):
        return False
    payload = unj(ev["payload"])
    if ev["event_type"] == "LISTING_PRICE_CHANGED" and payload.get("direction") != "DOWN":
        return False
    snap = None
    if ev["listing_id"]:
        mem = conn.execute("SELECT outcome FROM assessment WHERE subject_type='COMMUNITY_MEMBERSHIP' AND listing_id=? AND status='CURRENT'",
                           (ev["listing_id"],)).fetchone()
        if mem and mem["outcome"] not in unj(rule["membership_statuses"]):
            return False
        snap = conn.execute("SELECT * FROM listing_snapshot WHERE listing_id=? ORDER BY id DESC LIMIT 1", (ev["listing_id"],)).fetchone()
    price = payload.get("total_price") or payload.get("new_price") or (snap["total_price"] if snap else None)
    if rule["max_total_price"] and price and price > rule["max_total_price"]:
        return False
    if rule["min_price_drop_pct"] and ev["event_type"] == "LISTING_PRICE_CHANGED":
        if abs(payload.get("pct") or 0) * 100 < rule["min_price_drop_pct"]:
            return False
    if rule["min_rooms"] and snap and (snap["rooms"] or 0) < rule["min_rooms"]:
        return False
    if rule["min_building_ping"] and snap and (snap["building_ping"] or 0) < rule["min_building_ping"]:
        return False
    return True


def process(conn, outbox="outbox"):
    ensure_default_rules(conn)
    out = []
    rules = conn.execute("SELECT * FROM alert_rule WHERE enabled=1 AND deleted_at IS NULL").fetchall()
    events = conn.execute("SELECT * FROM listing_event WHERE notify_eligible=1 ORDER BY id").fetchall()
    for ev in events:
        for rule in rules:
            if conn.execute("SELECT 1 FROM notification WHERE alert_rule_id=? AND event_id=? AND notification_kind='EVENT'",
                            (rule["id"], ev["event_id"])).fetchone() or not _matches(conn, rule, ev):
                continue
            rendered = _render(conn, ev)
            status, err = _deliver(rule, rendered, outbox)
            conn.execute("INSERT INTO notification (notification_kind, alert_rule_id, event_id, channel, rendered, status, attempts,"
                         " last_error, sent_at) VALUES ('EVENT',?,?,?,?,?,1,?,?)",
                         (rule["id"], ev["event_id"], rule["channel"], j(rendered), status, err, now() if status == "SENT" else None))
            out.append({"event_id": ev["event_id"], "rule": rule["name"], "status": status})
    for n in conn.execute("SELECT n.*, r.name AS rule_name, r.channel AS rule_channel FROM notification n JOIN alert_rule r ON r.id=n.alert_rule_id"
                          " WHERE n.notification_kind='CORRECTION' AND n.attempts=0 ORDER BY n.id").fetchall():
        status, err = _deliver({"channel": n["rule_channel"], "id": f"correction{n['id']}"}, unj(n["rendered"], {}), outbox)
        conn.execute("UPDATE notification SET status=?, attempts=1, last_error=?, sent_at=? WHERE id=?",
                     (status, err, now() if status == "SENT" else None, n["id"]))
        out.append({"event_id": n["event_id"], "rule": n["rule_name"], "status": status, "kind": "CORRECTION"})
    conn.commit()
    return out


def corrections_for(conn, listing_ids, trigger_key):
    """已發過 DWELLING_NEW_LISTING 通知的刊登改變群組時，發更正通知（不重發新案）。"""
    trig = conn.execute("SELECT event_id FROM listing_event WHERE idempotency_key=?", (trigger_key,)).fetchone()
    if not trig or not listing_ids:
        return []
    made = []
    rows = conn.execute(
        f"SELECT n.*, r.notify_corrections FROM notification n JOIN listing_event e ON e.event_id=n.event_id"
        f" JOIN alert_rule r ON r.id=n.alert_rule_id WHERE n.notification_kind='EVENT' AND e.event_type='DWELLING_NEW_LISTING'"
        f" AND e.listing_id IN ({','.join('?' * len(listing_ids))})", list(listing_ids)).fetchall()
    for n in rows:
        if not n["notify_corrections"]:
            continue
        if conn.execute("SELECT 1 FROM notification WHERE notification_kind='CORRECTION' AND event_id=? AND corrects_notification_id=?",
                        (trig["event_id"], n["id"])).fetchone():
            continue
        rendered = {"subject": "[JEV] 更正", "text": f"先前的通知（#{n['id']}）所屬的同屋群組已變更（{trigger_key}）", "event_id": trig["event_id"]}
        nid = conn.execute("INSERT INTO notification (notification_kind, alert_rule_id, event_id, channel, rendered, status, attempts,"
                           " last_error, corrects_notification_id) VALUES ('CORRECTION',?,?,?,?, 'PENDING', 0, 'DRY_RUN_OUTBOX', ?)",
                           (n["alert_rule_id"], trig["event_id"], n["channel"], j(rendered), n["id"])).lastrowid
        made.append(nid)
    return made
