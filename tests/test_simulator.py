"""
Tests for Virtual JCR Simulator Engine.
"""

import pytest
from pathlib import Path
from aem_mcp.simulator.jcr_engine import JcrEngine

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def engine():
    data_path = ROOT / "data" / "jcr_mock_store.json"
    return JcrEngine(data_path)


def test_jcr_engine_loads_nodes(engine):
    assert engine.total_nodes > 100


def test_get_json_depth_0(engine):
    data = engine.get_json("/content/meridian/us/en", depth=0)
    assert data["jcr:primaryType"] == "cq:Page"
    assert "hotels" in data
    assert data["hotels"]["jcr:primaryType"] == "cq:Page"


def test_get_json_depth_1(engine):
    data = engine.get_json("/content/meridian/us/en", depth=1)
    assert data["jcr:primaryType"] == "cq:Page"
    assert "hotels" in data
    assert "jcr:content" in data["hotels"]


def test_querybuilder_by_type(engine):
    res = engine.querybuilder({
        "path": "/content/meridian/us/en/hotels",
        "type": "cq:Page",
        "p.limit": "50"
    })
    assert res["success"] is True
    assert res["total"] > 20
    assert len(res["hits"]) == min(res["total"], 50)
    for hit in res["hits"]:
        assert hit["jcr:path"].startswith("/content/meridian/us/en/hotels")


def test_querybuilder_property_filter(engine):
    res = engine.querybuilder({
        "path": "/content/meridian/us/en/hotels",
        "property": "hotelId",
        "property.value": "MDN-NYC-001"
    })
    assert res["success"] is True
    assert res["total"] == 1
    assert "new-york" in res["hits"][0]["jcr:path"]


def test_querybuilder_fulltext(engine):
    res = engine.querybuilder({
        "path": "/content/meridian/us/en/hotels",
        "fulltext": "grand",
        "p.limit": "10"
    })
    assert res["success"] is True
    assert res["total"] > 0


def test_find_references(engine):
    # Find any actual hotel page that has a hotelId
    res = engine.querybuilder({
        "path": "/content/meridian/us/en/hotels",
        "property": "hotelId",
        "property.operation": "exists",
        "p.limit": "1"
    })
    assert res["total"] > 0
    hotel_path = res["hits"][0]["jcr:path"]
    refs = engine.find_references(hotel_path)
    assert len(refs) > 0
    # Must reference template and DAM content fragment
    assert any("/conf/meridian/settings/wcm/templates" in r for r in refs)
    assert any("/content/dam/meridian/content-fragments" in r for r in refs)

