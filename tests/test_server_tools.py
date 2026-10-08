"""
Comprehensive test suite for AEM Content Intelligence FastMCP Tools.
"""

import json
import pytest
from aem_mcp.server import (
    registry_indexes,
    registry_search,
    registry_get,
    aem_json,
    aem_traverse,
    aem_querybuilder,
    aem_find_references,
    aem_discover_properties,
    aem_query_dataset,
    dataset_info,
    dataset_get_rows,
    dataset_analyze,
    dataset_match,
    dataset_export,
    dataset_discard,
    execute_code,
)


def test_registry_indexes():
    res = json.loads(registry_indexes())
    assert "properties" in res
    assert "brands" in res
    assert "destinations" in res
    assert res["properties"]["records"] > 50


def test_registry_search_and_get():
    # Search for Paris
    search_res = json.loads(registry_search(index="properties", city="Paris"))
    assert search_res["total"] > 0
    hotel = search_res["results"][0]
    hotel_id = hotel["hotel_id"]

    # Get single hotel
    get_res = json.loads(registry_get(index="properties", identifier=hotel_id))
    assert get_res["found"] is True
    assert get_res["record"]["hotel_id"] == hotel_id
    assert get_res["record"]["city"] == "Paris"


def test_aem_json_and_traverse():
    data = json.loads(aem_json(path="/content/meridian/us/en", depth=0))
    assert data["path"] == "/content/meridian/us/en"
    assert "hotels" in data["data"]

    trav = json.loads(aem_traverse(path="/content/meridian/us/en", depth=1))
    assert "hotels" in trav["data"]
    assert "jcr:content" in trav["data"]["hotels"]


def test_aem_querybuilder():
    res = json.loads(aem_querybuilder(query={
        "path": "/content/meridian/us/en/hotels",
        "type": "cq:Page",
        "p.limit": "5"
    }))
    assert res["data"]["success"] is True
    assert len(res["data"]["hits"]) <= 5


def test_aem_find_references():
    qb = json.loads(aem_querybuilder(query={
        "path": "/content/meridian/us/en/hotels",
        "property": "hotelId",
        "p.limit": "1"
    }))
    path = qb["data"]["hits"][0]["jcr:path"]
    ref_res = json.loads(aem_find_references(path=path))
    assert ref_res["reference_count"] > 0
    assert any("/conf/meridian" in r for r in ref_res["references"])


def test_aem_discover_properties():
    disc = json.loads(aem_discover_properties(path="/content/meridian/us/en/hotels"))
    assert disc["inspected_nodes"] > 0
    assert "properties" in disc
    assert "jcr:title" in disc["properties"]


def test_dataset_lifecycle_and_analysis():
    # 1. Query dataset
    ds_res = json.loads(aem_query_dataset(
        query={"path": "/content/meridian/us/en/hotels", "p.limit": "50"},
        properties=["hotelId", "authoredStarRating"]
    ))
    ds_id = ds_res["dataset_id"]
    assert ds_res["row_count"] > 10

    # 2. Dataset info & rows
    info = json.loads(dataset_info(dataset_id=ds_id))
    assert info["row_count"] == ds_res["row_count"]

    rows_res = json.loads(dataset_get_rows(dataset_id=ds_id, offset=0, limit=5))
    assert len(rows_res["rows"]) == 5

    # 3. Server-side analytics: Group By
    group_res = json.loads(dataset_analyze(
        dataset_id=ds_id,
        operation="group_by",
        field="authoredStarRating"
    ))
    assert group_res["total_rows"] == ds_res["row_count"]
    assert "groups" in group_res

    # 4. Export CSV & XLSX
    csv_exp = json.loads(dataset_export(dataset_id=ds_id, format="csv"))
    assert csv_exp["format"] == "csv"
    assert "download_url" in csv_exp

    xlsx_exp = json.loads(dataset_export(dataset_id=ds_id, format="xlsx"))
    assert xlsx_exp["format"] == "xlsx"
    assert "download_url" in xlsx_exp

    # 5. Clean up
    discard_res = json.loads(dataset_discard(dataset_id=ds_id))
    assert discard_res["deleted"] is True


def test_cross_system_dataset_match():
    """Test the killer feature: matching AEM authored content vs Property Master."""
    from aem_mcp.datasets.engine import materialize_dataset
    from aem_mcp.registry import search_registry

    # 1. Materialize AEM content
    aem_ds = json.loads(aem_query_dataset(
        query={"path": "/content/meridian/us/en/hotels", "p.limit": "50"},
        properties=["hotelId", "authoredStarRating"]
    ))
    aem_id = aem_ds["dataset_id"]

    # 2. Materialize Master Property records
    master_hotels = search_registry(index="properties", limit=150)["results"]
    master_id = materialize_dataset(
        rows=[{"hotel_id": h["hotel_id"], "star_rating": h["star_rating"]} for h in master_hotels],
        metadata={"source": "property_master"}
    )

    # 3. Match across systems on hotelId = hotel_id
    reconciliation = json.loads(dataset_match(
        left_dataset_id=aem_id,
        right_dataset_id=master_id,
        left_field="hotelId",
        right_field="hotel_id"
    ))

    assert reconciliation["total_rows"] > 0
    assert reconciliation["matched_count"] > 0
    assert "sample" in reconciliation

    # Clean up
    dataset_discard(aem_id)
    dataset_discard(master_id)
    dataset_discard(reconciliation["dataset_id"])


def test_execute_code_sandbox():
    # Valid math calculation
    calc = json.loads(execute_code(code="total = 40 + 2\nmessage = 'Audit Passed'"))
    assert calc["total"] == 42
    assert calc["message"] == "Audit Passed"

    # Blocked import
    with pytest.raises(ValueError, match="cannot import"):
        execute_code(code="import os\nos.listdir('.')")

    # Blocked function
    with pytest.raises(ValueError, match="blocked"):
        execute_code(code="open('test.txt', 'w')")
