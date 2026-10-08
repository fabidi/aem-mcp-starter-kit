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
    aem_audit_content_integrity,
    aem_audit_cross_reference,
    aem_audit_localization_coverage,
    aem_compile_querybuilder_sql2,
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
    data = json.loads(aem_json(path="/content/novaria/us/en", depth=0))
    assert data["path"] == "/content/novaria/us/en"
    assert "hotels" in data["data"]

    trav = json.loads(aem_traverse(path="/content/novaria/us/en", depth=1))
    assert "hotels" in trav["data"]
    assert "jcr:content" in trav["data"]["hotels"]


def test_aem_querybuilder():
    res = json.loads(aem_querybuilder(query={
        "path": "/content/novaria/us/en/hotels",
        "type": "cq:Page",
        "p.limit": "5"
    }))
    assert res["data"]["success"] is True
    assert len(res["data"]["hits"]) <= 5


def test_aem_find_references():
    qb = json.loads(aem_querybuilder(query={
        "path": "/content/novaria/us/en/hotels",
        "property": "hotelId",
        "p.limit": "1"
    }))
    path = qb["data"]["hits"][0]["jcr:path"]
    ref_res = json.loads(aem_find_references(path=path))
    assert ref_res["reference_count"] > 0
    assert any("/conf/novaria" in r for r in ref_res["references"])


def test_aem_discover_properties():
    disc = json.loads(aem_discover_properties(path="/content/novaria/us/en/hotels"))
    assert disc["inspected_nodes"] > 0
    assert "properties" in disc
    assert "jcr:title" in disc["properties"]


def test_dataset_lifecycle_and_analysis():
    # 1. Query dataset
    ds_res = json.loads(aem_query_dataset(
        query={"path": "/content/novaria/us/en/hotels", "p.limit": "50"},
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
        query={"path": "/content/novaria/us/en/hotels", "p.limit": "50"},
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


def test_query_execution_profiling():
    qb = json.loads(aem_querybuilder({"path": "/content/novaria/us/en/hotels", "p.limit": "10"}))
    assert "execution_time_ms" in qb
    assert isinstance(qb["execution_time_ms"], (int, float))

    ds = json.loads(aem_query_dataset({"path": "/content/novaria/us/en/hotels", "p.limit": "50"}, max_records=100))
    assert "execution_time_ms" in ds
    assert isinstance(ds["execution_time_ms"], (int, float))
    dataset_discard(ds["dataset_id"])


def test_aem_audit_content_integrity():
    # Regional US/EN audit
    res_us = json.loads(aem_audit_content_integrity(root_path="/content/novaria/us/en", limit=200))
    assert res_us["pages_audited"] == 1072
    summary_us = res_us["summary"]
    assert summary_us["broken_dam_references"] == 45
    assert summary_us["expired_promos"] == 50
    assert summary_us["incomplete_content_fragments"] == 35
    assert res_us["total_anomalies"] == 130

    # Global site audit with unique asset tracking
    res_global = json.loads(aem_audit_content_integrity(root_path="/content/novaria", limit=500))
    assert res_global["pages_audited"] > 5000
    assert res_global["summary"]["unique_broken_assets"] == 45


def test_aem_audit_cross_reference():
    res = json.loads(aem_audit_cross_reference(path="/content/novaria/us/en/hotels", limit=200))
    assert res["aem_pages_audited"] == 1020
    assert res["master_properties_count"] == 1000
    summary = res["summary"]
    assert summary["stale_star_ratings"] == 40
    assert summary["operating_status_mismatches"] == 25
    assert summary["amenity_desyncs"] == 35
    assert summary["orphan_pages"] == 20
    assert res["total_discrepancies"] == 120


def test_aem_audit_localization_coverage():
    res = json.loads(aem_audit_localization_coverage(base_locale="us/en", subpath="hotels"))
    assert res["canonical_pages"] == 1000
    locales = res["locales"]
    assert locales["fr/fr"]["missing_count"] == 60
    assert locales["de/de"]["missing_count"] == 40
    assert locales["gb/en"]["missing_count"] == 0
    assert locales["es/es"]["missing_count"] == 0
    assert locales["jp/ja"]["missing_count"] == 0


def test_aem_compile_querybuilder_sql2():
    res = json.loads(aem_compile_querybuilder_sql2({
        "path": "/content/novaria/us/en/hotels",
        "type": "cq:Page",
        "property": "hotelId",
        "property.value": "NVR-NYC-0001"
    }))
    assert "jcr_sql2" in res
    assert "SELECT" in res["jcr_sql2"]
    assert "ISDESCENDANTNODE" in res["jcr_sql2"]
    assert "NVR-NYC-0001" in res["jcr_sql2"]

