import sys
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jev import registry  # noqa: E402
from jev.db import connect  # noqa: E402
from jev.lifecycle import record  # noqa: E402
from jev.assess import assess_listing  # noqa: E402

SPEC = yaml.safe_load((ROOT / "api" / "openapi.yaml").read_text(encoding="utf-8"))
URI = "urn:jev:openapi"
REG = Registry().with_resource(URI, Resource.from_contents(SPEC, default_specification=DRAFT202012))


def validate(schema_name, data):
    v = Draft202012Validator({"$ref": f"{URI}#/components/schemas/{schema_name}"}, registry=REG)
    errors = sorted(v.iter_errors(data), key=lambda e: list(e.path))
    assert not errors, f"{schema_name}: {errors[0].message} at {list(errors[0].path)}"


@pytest.fixture
def conn():
    c = connect(":memory:")
    registry.load(c)
    return c


BASE = {"source_code": "SRC-591", "platform_community_ref": "591:9488", "community_text": "聯上世界",
        "address_text": "桃園市大園區高鐵南路一段", "parking_included": "INCLUDED"}


@pytest.fixture
def add(conn):
    counter = {"n": 0}

    def _add(mode="INITIAL", **fields):
        counter["n"] += 1
        rec = {**BASE, "external_id": fields.pop("external_id", f"T{counter['n']:04d}"), **fields}
        rec.setdefault("total_price", 20000000)
        res = record(conn, rec, mode)
        out = assess_listing(conn, res["listing_id"])
        conn.commit()
        return res["listing_id"], out
    return _add


def live(out):
    return sorted((c for c in out["address"]["candidates"] if not c["excluded"]), key=lambda c: (-c["score"], c["sales_unit"], c["floor"]))
