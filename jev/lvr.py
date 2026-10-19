"""官方實價登錄開放資料（本機檔案）篩選：只讀使用者已下載的檔案，不連網（docs/03 §7）。"""

import csv
import io
import re
import zipfile

from .normalize import cn_to_int, to_halfwidth

P = 0.3025  # 平方公尺 → 坪


def _rows_from(path):
    if str(path).lower().endswith(".zip"):
        with zipfile.ZipFile(path) as z:
            name = next(n for n in z.namelist() if n.lower() == "h_lvr_land_a.csv")
            text = z.read(name).decode("utf-8-sig")
    else:
        text = open(path, encoding="utf-8-sig").read()
    rows = list(csv.reader(io.StringIO(text)))
    head = rows[0]
    return [dict(zip(head, r)) for r in rows[2:]]


def filter_community(path, road="高鐵南路一段", numbers=range(77, 110, 2)):
    out = []
    for r in _rows_from(path):
        addr = to_halfwidth(r.get("土地位置建物門牌", ""))
        m = re.search(road + r"(\d+)號", addr)
        if not m or int(m.group(1)) not in numbers:
            continue
        floor = cn_to_int(r["移轉層次"].replace("層", "")) if r.get("移轉層次") else None
        bld = float(r["建物移轉總面積平方公尺"] or 0)
        park = float(r["車位移轉總面積平方公尺"] or 0)
        out.append({"number": int(m.group(1)), "floor": floor, "date": r["交易年月日"], "rooms": r["建物現況格局-房"],
                    "ping_ex_parking": round((bld - park) * P, 2), "main_ping": round(float(r["主建物面積"] or 0) * P, 2),
                    "total_price": int(r["總價元"] or 0), "parking_price": int(r["車位總價元"] or 0), "serial": r["編號"]})
    return out


def import_transactions(conn, path, season=None):
    """把本機實價登錄檔案中聯上世界的成交存進資料庫（依編號去重）。回傳（新增, 已存在）。"""
    from pathlib import Path

    from .db import now
    rows = filter_community(path)
    season = season or (re.search(r"(\d{3}S\d)", Path(path).name) or [None, None])[1]
    added = 0
    for r in rows:
        cur = conn.execute(
            "INSERT OR IGNORE INTO lvr_transaction (serial, season, number, floor, trade_date, rooms, ping_ex_parking, main_ping,"
            " total_price, parking_price, source_file, imported_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (r["serial"], season, r["number"], r["floor"], r["date"], r["rooms"], r["ping_ex_parking"], r["main_ping"],
             r["total_price"], r["parking_price"], Path(path).name, now()))
        added += cur.rowcount
    conn.commit()
    return added, len(rows) - added
