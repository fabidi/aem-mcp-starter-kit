"""
Unit tests for MCP Query Indexing Linter & Oak Plan Explanations.
"""

import json
from aem_mcp.server import (
    aem_lint_query_indexing,
    aem_querybuilder,
)
from aem_mcp.simulator.jcr_engine import JcrEngine
from aem_mcp.config import CONFIG


def test_lint_unindexed_query_traversal():
    """Verify that custom property queries without Oak index flag CRITICAL traversal risk."""
    query = {
        "path": "/content/novaria/us/en/hotels",
        "type": "cq:Page",
        "1_property": "hotelId",
        "1_property.value": "NVR-NYC-0001",
        "2_property": "brandId",
        "2_property.value": "novaria-grand",
    }
    raw = aem_lint_query_indexing(query)
    res = json.loads(raw)

    assert res["is_traversal"] is True
    assert res["risk_level"] == "CRITICAL"
    assert "hotelId" in res["unindexed_properties"]
    assert "brandId" in res["unindexed_properties"]
    assert "traverse" in res["simulated_oak_plan"]
    assert "scan limit" in res["diagnosis"]

    # Verify remediation indexes are generated
    remediations = res["recommended_indexes"]
    assert "property_index" in remediations
    assert "lucene_index" in remediations
    assert "hotelIdIndex" in remediations["property_index"]["path"]
    assert "hotelId" in remediations["property_index"]["json"]["propertyNames"]
    assert "filevault_xml" in remediations["property_index"]
    assert 'type="property"' in remediations["property_index"]["filevault_xml"]


def test_lint_optimal_fulltext_query():
    """Verify that fulltext queries utilizing Oak Lucene index are classified OPTIMAL."""
    query = {
        "path": "/content/novaria/us/en/hotels",
        "type": "cq:Page",
        "fulltext": "penthouse suite",
    }
    raw = aem_lint_query_indexing(query)
    res = json.loads(raw)

    assert res["is_traversal"] is False
    assert res["risk_level"] == "OPTIMAL"
    assert "lucene:lucene" in res["simulated_oak_plan"]


def test_querybuilder_explain_mode():
    """Verify that passing p.explain=true to aem_querybuilder returns Oak plan."""
    query = {
        "path": "/content/novaria/us/en/hotels",
        "type": "cq:Page",
        "property": "hotelId",
        "property.value": "NVR-NYC-0001",
        "p.explain": "true",
    }
    raw = aem_querybuilder(query)
    res = json.loads(raw)

    assert res["data"]["explain"] is True
    assert res["data"]["is_traversal"] is True
    assert "traverse" in res["data"]["plan"]
    assert res["data"]["risk_level"] == "CRITICAL"


def test_simulator_explain_nodetype_query():
    """Verify that querying purely by type uses nodetype index simulation."""
    engine = JcrEngine(CONFIG.mock_jcr_path)
    res = engine.querybuilder({
        "path": "/content/novaria",
        "type": "cq:Page",
        "p.explain": "true",
    })

    assert res["explain"] is True
    assert res["is_traversal"] is False
    assert res["index_used"] == "nodetype"
    assert "property:nodetype" in res["plan"]
