"""591 → JEV 書籤匯入：解析器（Node 執行）、書籤組裝、既有刊登的新觀測 API。"""

import json
import shutil
import subprocess
import urllib.parse

import pytest
from conftest import ROOT, validate

from jev import registry
from jev.api import App, bookmarklet

H = {"x-api-key": "k"}
NODE = shutil.which("node")


def _parse(page):
    js = ("const {parse591}=require(process.argv[1]);let s='';process.stdin.on('data',d=>s+=d);"
          "process.stdin.on('end',()=>process.stdout.write(JSON.stringify(parse591(JSON.parse(s)))));")
    out = subprocess.run([NODE, "-e", js, str(ROOT / "jev" / "ui" / "parse591.js")], input=json.dumps(page).encode("utf-8"),
                         capture_output=True, check=True)
    return json.loads(out.stdout.decode("utf-8"))


@pytest.mark.skipif(NODE is None, reason="需要 Node.js")
def test_parser_reads_fields_and_ignores_recommended_community():
    page = json.loads((ROOT / "tests" / "fixtures" / "591_page_sample.json").read_text(encoding="utf-8"))
    r = _parse(page)["record"]
    assert (r["external_id"], r["total_price"], r["building_ping"], r["parking_ping"], r["main_ping"]) == ("20790495", 21880000, 43.922, 7.807, 20.733)
    assert (r["floor_text"], r["rooms"], r["halls"], r["baths"]) == ("9樓/14樓", 2, 2, 1)  # 不採用 JSON-LD 的 numberOfRooms=3
    assert (r["community_text"], r["platform_community_ref"]) == ("聯上世界", "591:9488")  # 熱門社區「金滿意」被略過
    assert r["parking_included"] == "INCLUDED" and r["lister_type"] == "AGENT"
    assert set(r) <= {"source_code", "external_id", "url", "observed_at", "title", "total_price", "building_ping", "main_ping", "parking_ping",
                      "parking_included", "floor_text", "rooms", "halls", "baths", "age_years", "orientation", "address_text",
                      "community_text", "platform_community_ref", "lister_type", "lister_company"}  # 沒有任何個人資料欄位
    assert _parse(page)["photos"] == ["https://img1.591.com.tw/house/2026/08/25/a.jpeg!1000x.water2.jpeg"]  # 只收 591 圖片網域
    page["links"] = page["links"][:1]
    res = _parse(page)
    assert res["record"]["platform_community_ref"] is None and any("社區連結" in w for w in res["warnings"])


@pytest.mark.skipif(NODE is None, reason="需要 Node.js")
def test_bookmarklet_is_valid_javascript(tmp_path):
    b = bookmarklet("127.0.0.1:8787")
    assert b.startswith("javascript:") and "127.0.0.1:8787/#import=" in urllib.parse.unquote(b)
    f = tmp_path / "bm.js"
    f.write_text(urllib.parse.unquote(b[len("javascript:"):]), encoding="utf-8")
    subprocess.run([NODE, "--check", str(f)], check=True)


@pytest.fixture
def app(tmp_path):
    a = App(tmp_path / "i.db", api_key="k", outbox=str(tmp_path / "ob"))
    registry.load(a.conn)
    return a


def test_new_then_repeat_then_price_drop(app):
    snap = {"total_price": 21880000, "building_ping": 43.922, "parking_ping": 7.807, "parking_included": "INCLUDED", "floor_text": "9樓/14樓",
            "rooms": 2, "community_text": "聯上世界", "platform_community_ref": "591:9488", "address_text": "桃園市大園區高鐵南路一段"}
    body = {"source_code": "SRC-591", "external_id": "20790495", "import_mode": "INCREMENTAL", "lister_type": "AGENT", "snapshot": snap}
    validate("ListingCreate", body)
    status, created, _ = app.handle("POST", "/api/v1/listings", H, body)
    assert status == 201 and created["membership_status"] == "MEMBER"
    lid = created["id"]
    status, s1, _ = app.handle("POST", f"/api/v1/listings/{lid}/snapshots", H, snap)
    assert status == 200  # 內容沒變：不新增快照
    validate("Snapshot", s1)
    status, s2, _ = app.handle("POST", f"/api/v1/listings/{lid}/snapshots", H, {**snap, "total_price": 21280000})
    assert status == 201 and s2["total_price"] == 21280000
    ev = app.handle("GET", "/api/v1/events", H, {})[1]["items"]
    assert [e["payload"]["direction"] for e in ev if e["event_type"] == "LISTING_PRICE_CHANGED"] == ["DOWN"]
    status, err, _ = app.handle("POST", f"/api/v1/listings/{lid}/snapshots", H, {**snap, "agent_phone": "x"})
    assert status == 422  # 不接受未定義欄位


def test_tools_page(app):
    status, page, hdr = app.handle("GET", "/tools", {"host": "127.0.0.1:8787"}, {})
    assert status == 200 and 'href="javascript:' in page and hdr["Content-Type"].startswith("text/html")
