"""
Unit tests for Native SQLite JSON1 Dataset Analytics Engine.
Verifies server-side aggregation, numeric statistics, date/range operators,
and elimination of in-memory Python array ceilings.
"""

import json
import pytest
from aem_mcp.datasets.engine import (
    materialize_dataset,
    analyze_dataset,
    get_dataset_metadata,
    get_dataset_rows,
    discard_dataset,
)


@pytest.fixture
def sample_dataset_id():
    test_rows = [
        {"hotelId": "NVR-001", "city": "Paris", "price": 450.0, "starRating": 5, "updated": "2025-06-15", "brand": "Novaria Grand"},
        {"hotelId": "NVR-002", "city": "Paris", "price": 320.0, "starRating": 4, "updated": "2025-05-10", "brand": "Novaria House"},
        {"hotelId": "NVR-003", "city": "Tokyo", "price": 280.0, "starRating": 4, "updated": "2024-11-20", "brand": "Novaria House"},
        {"hotelId": "NVR-004", "city": "Tokyo", "price": 600.0, "starRating": 5, "updated": "2025-07-01", "brand": "Novaria Grand"},
        {"hotelId": "NVR-005", "city": "London", "price": 190.0, "starRating": 3, "updated": "2024-03-15", "brand": "Novaria Select"},
        {"hotelId": "NVR-006", "city": None, "price": None, "starRating": None, "updated": "", "brand": "Novaria Select"},
    ]
    ds_id = materialize_dataset(
        rows=test_rows,
        metadata={"source": "test_fixture", "record_count": len(test_rows)}
    )
    yield ds_id
    discard_dataset(ds_id)


def test_native_sql_missing_operation(sample_dataset_id):
    res = analyze_dataset(sample_dataset_id, operation="missing", field="city")
    assert res["total_rows"] == 6
    assert res["missing_count"] == 1
    assert res["present_count"] == 5
    assert res["completeness_pct"] == 83.33


def test_native_sql_group_by_operation(sample_dataset_id):
    res = analyze_dataset(sample_dataset_id, operation="group_by", field="city")
    assert res["total_rows"] == 6
    assert res["distinct_values"] == 4
    assert res["groups"]["Paris"] == 2
    assert res["groups"]["Tokyo"] == 2
    assert res["groups"]["London"] == 1
    assert res["groups"]["<MISSING>"] == 1


def test_native_sql_numeric_stats(sample_dataset_id):
    res = analyze_dataset(sample_dataset_id, operation="stats", field="price")
    assert res["total_rows"] == 6
    assert res["numeric_rows"] == 5
    assert res["min"] == 190.0
    assert res["max"] == 600.0
    assert res["sum"] == 1840.0
    assert res["avg"] == 368.0

    # Test direct single scalar operations
    avg_res = analyze_dataset(sample_dataset_id, operation="avg", field="price")
    assert avg_res["avg"] == 368.0
    assert avg_res["result"] == 368.0

    max_res = analyze_dataset(sample_dataset_id, operation="max", field="price")
    assert max_res["max"] == 600.0


def test_native_sql_numeric_comparison_operators(sample_dataset_id):
    # GT
    gt_res = analyze_dataset(sample_dataset_id, operation="count", field="price", operator="gt", value="300")
    assert gt_res["matching_rows"] == 3  # 450, 320, 600

    # GTE
    gte_res = analyze_dataset(sample_dataset_id, operation="count", field="price", operator="gte", value="320")
    assert gte_res["matching_rows"] == 3  # 450, 320, 600

    # LT
    lt_res = analyze_dataset(sample_dataset_id, operation="count", field="price", operator="lt", value="300")
    assert lt_res["matching_rows"] == 2  # 280, 190

    # BETWEEN
    between_res = analyze_dataset(sample_dataset_id, operation="count", field="price", operator="between", value="200,500")
    assert between_res["matching_rows"] == 3  # 450, 320, 280


def test_native_sql_date_operators(sample_dataset_id):
    # AFTER
    after_res = analyze_dataset(sample_dataset_id, operation="count", field="updated", operator="after", value="2025-01-01")
    assert after_res["matching_rows"] == 3  # 2025-06-15, 2025-05-10, 2025-07-01

    # BEFORE
    before_res = analyze_dataset(sample_dataset_id, operation="count", field="updated", operator="before", value="2025-01-01")
    assert before_res["matching_rows"] == 2  # 2024-11-20, 2024-03-15


def test_native_sql_text_operators(sample_dataset_id):
    # EQUALS
    eq_res = analyze_dataset(sample_dataset_id, operation="count", field="brand", operator="equals", value="novaria grand")
    assert eq_res["matching_rows"] == 2

    # CONTAINS
    contains_res = analyze_dataset(sample_dataset_id, operation="count", field="brand", operator="contains", value="house")
    assert contains_res["matching_rows"] == 2

    # STARTS_WITH
    starts_res = analyze_dataset(sample_dataset_id, operation="count", field="brand", operator="starts_with", value="Novaria")
    assert starts_res["matching_rows"] == 6

    # EXISTS & NOT
    exists_res = analyze_dataset(sample_dataset_id, operation="count", field="price", operator="exists")
    assert exists_res["matching_rows"] == 5

    not_res = analyze_dataset(sample_dataset_id, operation="count", field="price", operator="not")
    assert not_res["matching_rows"] == 1


def test_native_sql_filter_operation(sample_dataset_id):
    res = analyze_dataset(sample_dataset_id, operation="filter", field="city", operator="equals", value="Paris")
    assert res["matched_count"] == 2
    assert len(res["sample"]) == 2
    assert res["sample"][0]["hotelId"] in ("NVR-001", "NVR-002")
