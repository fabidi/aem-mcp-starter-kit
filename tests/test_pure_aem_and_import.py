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
