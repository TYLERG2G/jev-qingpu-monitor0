from jev.normalize import area_readings, normalize_address, parse_floor


def test_floor_formats():  # T-NORM-08/09
    assert (parse_floor("12F/14F").floor_min, parse_floor("12F/14F").floor_exact, parse_floor("12F/14F").total_floors) == (12, True, 14)
    assert parse_floor("9樓／14樓").floor_min == 9
    assert parse_floor("十二層").floor_min == 12
    f = parse_floor("10~15樓")
    assert (f.floor_min, f.floor_max, f.floor_exact) == (10, 15, False)
    vague = parse_floor("中樓層")
    assert (vague.floor_min, vague.floor_max, vague.floor_exact) == (None, None, False)  # 模糊樓層不轉成確定樓層


def test_address_full_and_partial():  # T-NORM-01～07
    a = normalize_address("桃園市大園區高鐵南路一段７９號十二樓")
    assert (a.city, a.district, a.road, a.section, a.number, a.floor, a.precision) == ("桃園市", "大園區", "高鐵南路", 1, 79, "12", "FLOOR")
    assert a.addr_key == "高鐵南路|1|||79||12|"
    assert normalize_address("高鐵南路一段79-1號").number_sub == 1
    road = normalize_address("桃園市大園區高鐵南路一段")
    assert (road.road, road.section, road.precision) == ("高鐵南路", 1, "ROAD")
    assert normalize_address("臺北市信義區忠孝東路五段68號").road == "忠孝東路"


def test_area_basis():  # 16 §3 雙口徑
    assert area_readings(43.922, 7.807, "INCLUDED") == [("INCLUDED", 36.12, False)]
    assert [r[0] for r in area_readings(43.56, None, "UNKNOWN")] == ["INCLUDED", "EXCLUDED"]
    assert area_readings(36.1, None, "EXCLUDED") == [("EXCLUDED", 36.1, False)]
