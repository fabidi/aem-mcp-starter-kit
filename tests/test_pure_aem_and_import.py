"""
Tests for Direction 3: Decoupled Pure AEM Mode and Generic Ingestion Bridge (dataset_import).
"""

import json
from pathlib import Path
import pytest

from aem_mcp.datasets.engine import (
    import_dataset_file,
    get_dataset_rows,
    get_dataset_metadata,
    match_datasets,
    discard_dataset,
    materialize_dataset,
)
from aem_mcp.server import dataset_import, mcp, aem_prompt_resource, aem_runtime_resource
from aem_mcp.config import Config


def test_import_dataset_file_csv(tmp_path: Path):
    csv_file = tmp_path / "enterprise_products.csv"
    csv_file.write_text(
        "sku,product_name,official_price,stock_level\n"
        "SKU-001,Alpine Pro Jacket,249.99,142\n"
        "SKU-002,Summit Hiking Boot,189.50,58\n"
        "SKU-003,Trailhead Backpack,89.00,0\n",
        encoding="utf-8"
    )

    res = import_dataset_file(file_path=csv_file, fmt="csv")
    ds_id = res["dataset_id"]
    try:
        assert ds_id.startswith("ds_")
        assert res["row_count"] == 3
        assert res["format"] == "csv"

        rows = get_dataset_rows(ds_id)
        assert len(rows) == 3
        assert rows[0]["sku"] == "SKU-001"
        assert rows[0]["product_name"] == "Alpine Pro Jacket"
        assert rows[0]["official_price"] == "249.99"
    finally:
        discard_dataset(ds_id)


def test_import_dataset_file_json(tmp_path: Path):
    json_file = tmp_path / "sap_inventory.json"
    data = [
        {"item_id": "ITM-10", "facility": "Warehouse A", "available": True},
        {"item_id": "ITM-20", "facility": "Warehouse B", "available": False},
    ]
    json_file.write_text(json.dumps(data), encoding="utf-8")

    res = import_dataset_file(file_path=json_file, fmt="auto")
    ds_id = res["dataset_id"]
    try:
        assert res["format"] == "json"
        assert res["row_count"] == 2
        rows = get_dataset_rows(ds_id)
        assert rows[1]["item_id"] == "ITM-20"
        assert rows[1]["available"] is False
    finally:
        discard_dataset(ds_id)


def test_import_dataset_file_jsonl(tmp_path: Path):
    jsonl_file = tmp_path / "events.jsonl"
    lines = [
        json.dumps({"event_id": "EVT-1", "severity": "HIGH"}),
        json.dumps({"event_id": "EVT-2", "severity": "LOW"}),
    ]
    jsonl_file.write_text("\n".join(lines), encoding="utf-8")

    res = import_dataset_file(file_path=jsonl_file, fmt="auto")
    ds_id = res["dataset_id"]
    try:
        assert res["format"] == "jsonl"
        assert res["row_count"] == 2
        rows = get_dataset_rows(ds_id)
        assert rows[0]["event_id"] == "EVT-1"
    finally:
        discard_dataset(ds_id)


def test_dataset_import_mcp_tool(tmp_path: Path):
    csv_file = tmp_path / "snowflake_feed.csv"
    csv_file.write_text("hotel_id,authoritative_star_rating\nHTL-PAR-001,5\nHTL-LON-002,4\n", encoding="utf-8")

    raw = dataset_import(file_path=str(csv_file))
    res = json.loads(raw)
    assert "dataset_id" in res
    assert res["row_count"] == 2
    discard_dataset(res["dataset_id"])


def test_reconcile_imported_external_dataset(tmp_path: Path):
    # Simulate AEM pages dataset
    aem_ds = materialize_dataset(
        rows=[
            {"path": "/content/site/p1", "hotel_id": "HTL-1", "authoredRating": "4"},
            {"path": "/content/site/p2", "hotel_id": "HTL-2", "authoredRating": "3"},
        ],
        metadata={"source": "aem_pages"}
    )

    # Simulate External PIM/ERP dataset imported via file
    ext_file = tmp_path / "external_master.csv"
    ext_file.write_text(
        "hotel_id,star_rating\n"
        "HTL-1,5\n"  # Discrepancy: AEM says 4, Master says 5
        "HTL-2,3\n", # Matches
        encoding="utf-8"
    )
    ext_res = import_dataset_file(file_path=ext_file)
    ext_ds = ext_res["dataset_id"]

    match_res = None
    try:
        match_res = match_datasets(
            left_dataset_id=aem_ds,
            right_dataset_id=ext_ds,
            left_field="hotel_id",
            right_field="hotel_id"
        )
        assert match_res["matched_count"] == 2
        assert match_res["total_rows"] == 2
    finally:
        discard_dataset(aem_ds)
        discard_dataset(ext_ds)
        if match_res and "dataset_id" in match_res:
            discard_dataset(match_res["dataset_id"])


def test_pure_aem_mode_flag_and_tool_filtering(monkeypatch):
    monkeypatch.setenv("AEM_MCP_ENABLE_REGISTRY", "false")
    cfg = Config()
    assert cfg.enable_registry is False
    assert cfg.is_pure_aem() is True


def test_runtime_and_prompt_in_pure_aem_mode(monkeypatch):
    import aem_mcp.server
    import aem_mcp.prompts
    monkeypatch.setattr(aem_mcp.server.CONFIG, "enable_registry", False)

    prompt = aem_prompt_resource()
    assert "Pure AEM Mode" in prompt
    assert "dataset_import" in prompt

    runtime = json.loads(aem_runtime_resource())
    assert runtime["pure_aem_mode"] == "True"


def test_pure_aem_tool_list_omits_registry_tools():
    import subprocess
    import sys
    script = (
        "import os, asyncio; "
        "os.environ['AEM_MCP_ENABLE_REGISTRY'] = 'false'; "
        "import aem_mcp.server; "
        "tools = asyncio.run(aem_mcp.server.mcp.list_tools()); "
        "names = [t.name for t in tools]; "
        "assert 'registry_indexes' not in names; "
        "assert 'domain_switch' not in names; "
        "assert 'dataset_import' in names; "
        "assert 'aem_querybuilder' in names; "
        "print('PURE_AEM_TOOLS_VERIFIED')"
    )
    res = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=True)
    assert "PURE_AEM_TOOLS_VERIFIED" in res.stdout


def test_execute_code_sandbox_and_timeout():
    from aem_mcp.server import execute_code

    # 1. Valid calculation succeeds
    out = json.loads(execute_code("total = sum([10, 20, 30])"))
    assert out["total"] == 60

    # 2. Dunder attribute introspection is blocked
    with pytest.raises(ValueError, match="dunder"):
        execute_code("x = ().__class__.__base__")

    # 3. getattr / globals / vars bypasses are blocked
    with pytest.raises(ValueError, match="blocked"):
        execute_code("x = getattr((), 'anything')")

    # 4. Timeout enforcement stops slow/stuck loops
    with pytest.raises(TimeoutError, match="timed out"):
        execute_code("for _ in range(200_000_000):\n    pass", timeout_seconds=1)


def test_domain_discovery_skips_template_folder():
    from aem_mcp.domains.manager import DOMAIN_MANAGER

    assert "_template" not in DOMAIN_MANAGER.profiles
    assert {"hospitality", "automotive", "retail"}.issubset(set(DOMAIN_MANAGER.profiles.keys()))


def test_generic_ast_rule_evaluator_custom_conditions():
    from aem_mcp.domains.manager import DomainManager

    # Custom numeric/currency comparison not previously in hardcoded list
    triggered, msg = DomainManager._evaluate_rule_condition(
        condition="aem.warrantyYears != registry.warranty_years",
        node_data={"warrantyYears": "3"},
        canon_rec={"warranty_years": "5"},
        default_msg="Warranty mismatch",
    )
    assert triggered is True
    assert "Warranty mismatch" in msg
    assert "aem.warrantyYears='3'" in msg

    # Compound boolean condition with < and >
    triggered2, _ = DomainManager._evaluate_rule_condition(
        condition="aem.price < registry.min_map_price or aem.discountPct > 40",
        node_data={"price": "$199.00", "discountPct": 50},
        canon_rec={"min_map_price": "$250.00"},
        default_msg="MAP violation",
    )
    assert triggered2 is True

    # Fallback when only aem_property and registry_column are provided
    triggered3, _ = DomainManager._evaluate_rule_condition(
        condition="",
        node_data={"skuCode": "ABC"},
        canon_rec={"sku_code": "XYZ"},
        default_msg="SKU mismatch",
        aem_property="skuCode",
        registry_column="sku_code",
    )
    assert triggered3 is True


def test_live_mode_audit_tools_without_simulator(monkeypatch):
    import aem_mcp.server as srv
    from aem_mcp.audit import audit_content_integrity, audit_localization_coverage

    sim = srv._SIMULATOR
    assert sim is not None

    # Test audit_content_integrity with all_nodes=None and query_runner
    res_integrity = audit_content_integrity(
        node_getter=lambda p: sim.get_json(p),
        root_path="/content/novaria/us/en/hotels",
        all_nodes=None,
        query_runner=lambda q: sim.querybuilder(q),
    )
    assert res_integrity["pages_audited"] > 0
    assert res_integrity["total_anomalies"] > 0

    # Test audit_localization_coverage with all_nodes=None and query_runner
    res_loc = audit_localization_coverage(
        all_nodes=None,
        base_locale="us/en",
        target_locales=["fr/fr"],
        subpath="hotels",
        query_runner=lambda q: sim.querybuilder(q),
    )
    assert res_loc["canonical_pages"] > 0
    assert "fr/fr" in res_loc["locales"]

    # Test aem_audit_domain_rules when _SIMULATOR is None (using _LiveJcrAdapter)
    monkeypatch.setattr(srv, "_SIMULATOR", None)
    monkeypatch.setattr(
        srv,
        "_get_aem_json",
        lambda path, params=None: sim.querybuilder(params or {})
        if path.startswith("/bin/querybuilder")
        else sim.get_json(path.replace(".1.json", "")),
    )
    domain_res = json.loads(srv.aem_audit_domain_rules(domain="hospitality"))
    assert domain_res["total_findings"] >= 2


def test_import_dataset_file_rejects_dotfiles_and_invalid_extensions(tmp_path: Path):
    dot_file = tmp_path / ".env"
    dot_file.write_text("SECRET=123\n", encoding="utf-8")
    with pytest.raises(ValueError, match="dot-files"):
        import_dataset_file(dot_file)

    bad_ext = tmp_path / "script.py"
    bad_ext.write_text("print('hi')\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Unsupported file extension"):
        import_dataset_file(bad_ext)


def test_lint_query_indexing_includes_repoinit():
    from aem_mcp.server import aem_lint_query_indexing

    res = json.loads(aem_lint_query_indexing({
        "path": "/content/novaria",
        "type": "cq:Page",
        "property": "jcr:content/hotelId",
        "property.value": "HTL-001",
    }))
    prop_idx = res["recommended_indexes"]["property_index"]
    assert "repoinit" in prop_idx
    assert "create path /oak:index/hotelIdIndex(oak:QueryIndexDefinition)" in prop_idx["repoinit"]

