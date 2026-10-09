"""
Tests for Multi-Domain Profiles and Pluggable Data Sources (SQLite, CSV, JSON).
"""

import json
import pytest
from aem_mcp.domains.manager import DOMAIN_MANAGER
from aem_mcp.server import (
    domain_list,
    domain_switch,
    domain_get_active,
    aem_audit_domain_rules,
    registry_indexes,
    registry_search,
    registry_get,
)


@pytest.fixture(autouse=True)
def ensure_default_domain():
    """Ensure tests always start and end in hospitality domain."""
    DOMAIN_MANAGER.switch_domain("hospitality")
    yield
    DOMAIN_MANAGER.switch_domain("hospitality")


def test_domain_discovery():
    assert "hospitality" in DOMAIN_MANAGER.profiles
    assert "automotive" in DOMAIN_MANAGER.profiles
    assert "retail" in DOMAIN_MANAGER.profiles

    hosp = DOMAIN_MANAGER.profiles["hospitality"]
    assert hosp.data_source_type == "sqlite"
    assert hosp.primary_entity == "Hotel"

    auto = DOMAIN_MANAGER.profiles["automotive"]
    assert auto.data_source_type == "csv"
    assert auto.primary_entity == "Vehicle"

    ret = DOMAIN_MANAGER.profiles["retail"]
    assert ret.data_source_type == "json"
    assert ret.primary_entity == "Product"


def test_domain_list_mcp_tool():
    raw = domain_list()
    res = json.loads(raw)
    assert res["active_domain"] == "hospitality"
    assert res["total_domains"] >= 3
    domain_ids = [d["id"] for d in res["domains"]]
    assert "hospitality" in domain_ids
    assert "automotive" in domain_ids
    assert "retail" in domain_ids


def test_domain_switch_and_get_active():
    # Switch to automotive
    switch_raw = domain_switch("automotive")
    switch_res = json.loads(switch_raw)
    assert switch_res["success"] is True
    assert switch_res["switched_to"] == "automotive"
    assert switch_res["data_source_type"] == "csv"

    # Get active profile info
    active_raw = domain_get_active()
    active_res = json.loads(active_raw)
    assert active_res["id"] == "automotive"
    assert active_res["primary_entity"] == "Vehicle"
    assert "vehicles" in active_res["tables"]
    assert len(active_res["audit_rules"]) >= 3


def test_automotive_csv_registry_queries():
    domain_switch("automotive")

    # Table indexes
    indexes_res = json.loads(registry_indexes())
    assert "vehicles" in indexes_res
    assert indexes_res["vehicles"]["records"] == 12
    assert indexes_res["vehicles"]["type"] == "csv"

    # Search
    search_res = json.loads(registry_search(index="vehicles", query="Solace"))
    assert search_res["total"] == 2
    assert "Apex Solace" in search_res["results"][0]["name"]

    # Filter by category
    filter_res = json.loads(registry_search(index="vehicles", query="Electric"))
    assert filter_res["total"] == 2
    assert filter_res["results"][0]["vin_prefix"] == "APX-EV-03"

    # Get by ID (VIN prefix)
    get_res = json.loads(registry_get(index="vehicles", identifier="APX-EV-03"))
    assert get_res["found"] is True
    assert get_res["record"]["name"] == "Apex Pulse EV"
    assert get_res["record"]["base_msrp"] == "54000"


def test_retail_json_registry_queries():
    domain_switch("retail")

    # Table indexes
    indexes_res = json.loads(registry_indexes())
    assert "products" in indexes_res
    assert indexes_res["products"]["records"] == 12
    assert indexes_res["products"]["type"] == "json"

    # Search
    search_res = json.loads(registry_search(index="products", query="Gore-Tex"))
    assert search_res["total"] == 1
    assert search_res["results"][0]["sku"] == "LUM-JKT-01"

    # Get by ID (SKU)
    get_res = json.loads(registry_get(index="products", identifier="LUM-BOOT-05"))
    assert get_res["found"] is True
    assert get_res["record"]["title"] == "Lumina Summit Waterproof Hiker"
    assert get_res["record"]["in_stock"] == "false"


def test_declarative_audit_rules_automotive():
    raw = aem_audit_domain_rules(domain="automotive")
    res = json.loads(raw)
    assert res["domain"] == "automotive"
    assert res["data_source_type"] == "csv"
    assert res["total_findings"] == 3

    rule_ids = [f["rule_id"] for f in res["findings"]]
    assert "msrp_desync" in rule_ids
    assert "broken_exterior_render" in rule_ids
    assert "discontinued_trim_live" in rule_ids


def test_declarative_audit_rules_retail():
    raw = aem_audit_domain_rules(domain="retail")
    res = json.loads(raw)
    assert res["domain"] == "retail"
    assert res["data_source_type"] == "json"
    assert res["total_findings"] == 3

    rule_ids = [f["rule_id"] for f in res["findings"]]
    assert "price_desync" in rule_ids
    assert "out_of_stock_live" in rule_ids
    assert "missing_product_image" in rule_ids
