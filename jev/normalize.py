"""正規化：樓層區間、地址、坪數口徑（docs/02 §3–4、docs/16 §3）。"""

import re
from dataclasses import dataclass

FULLWIDTH = str.maketrans("０１２３４５６７８９ＡＢＣＤＦ－／～", "0123456789ABCDF-/~")
CN_DIGITS = {"零": 0, "一": 1, "二": 2, "兩": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def cn_to_int(text: str):
    """十二 → 12；九 → 9；一百零九 → 109（只處理門牌與樓層會用到的範圍）。"""
    if text.isdigit():
        return int(text)
    total, section = 0, 0
    for ch in text:
        if ch in CN_DIGITS:
            section = CN_DIGITS[ch]
        elif ch == "十":
            total += (section or 1) * 10
            section = 0
        elif ch == "百":
            total += (section or 1) * 100
            section = 0
        else:
            return None
    return total + section


def to_halfwidth(text: str) -> str:
    return text.translate(FULLWIDTH) if text else text


def replace_cn_numbers(text: str) -> str:
    return re.sub(r"[零一二兩三四五六七八九十百]+(?=[段號樓層巷弄])", lambda m: str(cn_to_int(m.group(0))), text)


@dataclass
class FloorObservation:
    floor_text: str | None
    floor_min: int | None
    floor_max: int | None
    floor_exact: bool
    total_floors: int | None


def parse_floor(text) -> FloorObservation:
    """12F/14F、9樓／14樓、十二層、10~15樓 → 區間；中樓層等模糊描述不轉成確定樓層。"""
    if not text:
        return FloorObservation(None, None, None, False, None)
    raw = str(text).strip()
    t = replace_cn_numbers(to_halfwidth(raw)).replace(" ", "").replace("層", "樓").upper()
    total = None
    m_total = re.search(r"[/](\d{1,2})(?:F|樓)?$", t)
    if m_total:
        total = int(m_total.group(1))
        t = t[: m_total.start()]
    m_range = re.fullmatch(r"(\d{1,2})(?:F|樓)?[~\-](\d{1,2})(?:F|樓)?", t)
    if m_range:
        lo, hi = sorted((int(m_range.group(1)), int(m_range.group(2))))
        return FloorObservation(raw, lo, hi, lo == hi, total)
    m_one = re.fullmatch(r"(\d{1,2})(?:F|樓)", t) or re.fullmatch(r"(\d{1,2})", t)
    if m_one:
        f = int(m_one.group(1))
        return FloorObservation(raw, f, f, True, total)
    return FloorObservation(raw, None, None, False, total)


@dataclass
class NormalizedAddress:
    city: str | None
    district: str | None
    road: str | None
    section: int | None
    lane: str | None
    alley: str | None
    number: int | None
    number_sub: int | None
    floor: str | None
    floor_sub: int | None
    precision: str
    addr_key: str | None

    def to_dict(self):
        return dict(self.__dict__)


ROAD_RX = re.compile(r"([一-鿿]{1,6}?(?:路|街|大道))(?:(\d+)段)?")


def normalize_address(text) -> NormalizedAddress:
    if not text:
        return NormalizedAddress(None, None, None, None, None, None, None, None, None, None, "NONE", None)
    t = replace_cn_numbers(to_halfwidth(str(text))).replace(" ", "").replace("臺", "台")
    t = re.sub(r"(\d+)-(\d+)號", r"\1之\2號", t)
    city = district = None
    m = re.match(r"(.{2}[市縣])", t)
    if m:
        city, t = m.group(1), t[m.end():]
    m = re.match(r"([\u4e00-\u9fff]{2,3}?[區鄉鎮])", t)
    if m:
        district, t = m.group(1), t[m.end():]
    t = re.sub(r"^.{1,3}里(\d+鄰)?", "", t)
    road = section = lane = alley = number = number_sub = floor = floor_sub = None
    m = ROAD_RX.search(t)
    if m:
        road = m.group(1)
        section = int(m.group(2)) if m.group(2) else None
        t = t[m.end():]
    m = re.match(r"(\d+)巷", t)
    if m:
        lane, t = m.group(1), t[m.end():]
    m = re.match(r"(\d+)弄", t)
    if m:
        alley, t = m.group(1), t[m.end():]
    m = re.match(r"(\d+)(?:之(\d+))?號", t)
    if m:
        number = int(m.group(1))
        number_sub = int(m.group(2)) if m.group(2) else None
        t = t[m.end():]
    m = re.match(r"(B?\d+)(?:F|樓)(?:之(\d+))?", t.upper())
    if m:
        floor = m.group(1)
        floor_sub = int(m.group(2)) if m.group(2) else None
    if floor:
        precision = "FLOOR"
    elif number:
        precision = "NUMBER"
    elif lane:
        precision = "LANE"
    elif road:
        precision = "ROAD"
    else:
        precision = "NONE"
    key = None
    if road:
        parts = [road, section, lane, alley, number, number_sub, floor, floor_sub]
        key = "|".join("" if p is None else str(p) for p in parts)
    return NormalizedAddress(city, district, road, section, lane, alley, number, number_sub, floor, floor_sub, precision, key)


DEFAULT_SINGLE_PARKING_PING = 7.8  # 591 成交中單一平面車位的常見坪數（推論）


def _r2(x):
    """四捨五入到小數兩位（避免 36.115 因浮點誤差變成 36.11）。"""
    from decimal import ROUND_HALF_UP, Decimal
    return float(Decimal(str(round(x, 6))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def area_readings(building_ping, parking_ping, parking_included):
    """雙口徑試算（16 §3）：回傳 [(basis, 扣除車位後坪數, 是否為推論)]。"""
    if building_ping is None:
        return []
    readings = []
    if parking_included in ("INCLUDED", "UNKNOWN"):
        if parking_ping:
            readings.append(("INCLUDED", _r2(building_ping - parking_ping), parking_included == "UNKNOWN"))
        else:
            readings.append(("INCLUDED", _r2(building_ping - DEFAULT_SINGLE_PARKING_PING), True))
    if parking_included in ("EXCLUDED", "UNKNOWN"):
        readings.append(("EXCLUDED", _r2(building_ping), parking_included != "EXCLUDED"))
    seen, out = set(), []
    for r in readings:
        if r[:2] not in seen:
            seen.add(r[:2])
            out.append(r)
    return out


ORIENTATION_WORDS = {"東北": "NE", "西北": "NW", "東南": "SE", "西南": "SW", "北": "N", "南": "S", "東": "E", "西": "W"}


def parse_orientation(text):
    if not text:
        return None
    for word, code in ORIENTATION_WORDS.items():
        if word in text:
            return code
    return None
