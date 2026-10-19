"""展示用資料庫：13 則真實人工記錄 ＋ 明確標示的模擬刊登（external_id 以 DEMO- 開頭、標題含【DEMO 模擬】）。

模擬資料只用來展示事件、通知、同屋覆核的流程，不寫入正式資料庫，也不修改任何真實刊登。
"""

from pathlib import Path

from . import identity, lifecycle, notify, registry

ROOT = Path(__file__).resolve().parent.parent
SEED = ROOT / "data" / "seed" / "lianshang-listings-2026-10-18.csv"
BASE = {"community_text": "聯上世界", "address_text": "桃園市大園區高鐵南路一段", "parking_included": "INCLUDED", "lister_type": "AGENT"}

# 第一批（2026-10-25）：兩則「新刊登」
BATCH_1 = [
    {**BASE, "source_code": "SRC-591", "external_id": "DEMO-001", "platform_community_ref": "591:9488", "observed_at": "2026-10-25T09:00:00Z",
     "title": "【DEMO 模擬】聯上世界高樓兩房車位", "total_price": 21800000, "building_ping": 43.92, "parking_ping": 7.81,
     "floor_text": "9樓/14樓", "rooms": 2, "halls": 2, "baths": 1, "is_new_listing": "true"},
    {**BASE, "source_code": "SRC-YUNGCHING", "external_id": "DEMO-002", "observed_at": "2026-10-25T09:05:00Z",
     "title": "【DEMO 模擬】聯上世界三房", "total_price": 25800000, "building_ping": 51.37, "parking_ping": 7.81,
     "floor_text": "7樓/14樓", "rooms": 3, "halls": 2, "baths": 2, "is_new_listing": "true"},
]
# 第二批（2026-11-01）：DEMO-001 降價
BATCH_2 = [{**BATCH_1[0], "observed_at": "2026-11-01T09:00:00Z", "total_price": 21280000}]


def build(conn, outbox):
    registry.load(conn)
    lifecycle.import_csv(conn, SEED, "INITIAL")
    identity.evaluate_pairs(conn)
    notify.process(conn, outbox)  # 初始化匯入：0 則
    for batch in (BATCH_1, BATCH_2):
        for rec in batch:
            res = lifecycle.record(conn, dict(rec), "INCREMENTAL", observed_at=rec["observed_at"])
            if res["changed"]:
                from .assess import assess_listing
                assess_listing(conn, res["listing_id"])
        identity.evaluate_pairs(conn)
        notify.process(conn, outbox)
    conn.commit()
    return {
        "listings": conn.execute("SELECT COUNT(*) FROM listing").fetchone()[0],
        "notifications": conn.execute("SELECT COUNT(*) FROM notification").fetchone()[0],
        "tasks": conn.execute("SELECT COUNT(*) FROM review_task WHERE status='OPEN' AND priority < 5").fetchone()[0],
    }
