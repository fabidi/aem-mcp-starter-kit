"""
AEM Content Intelligence MCP Server.

A production-grade Model Context Protocol (MCP) server for Adobe Experience Manager (AEM).
Provides read-only access to AEM JCR, QueryBuilder, and Master Entity Registry with
in-memory SQLite dataset materialization, server-side analytics, and Excel/CSV export.
"""

import ast
import json
import os
import re
import urllib.parse
import urllib.request
import base64
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
try:
    from fastmcp import FastMCP
except ImportError:
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError:
        from mcp.server.mcpserver import MCPServer as FastMCP

import time
from aem_mcp.auth.ims import AdobeImsAuthProvider
from aem_mcp.config import CONFIG, ROOT_DIR
from aem_mcp.prompts import CANONICAL_PROMPT, get_runtime_info
from aem_mcp.registry import get_registry_indexes, search_registry, get_registry_entity
from aem_mcp.simulator.jcr_engine import JcrEngine
from aem_mcp.audit import (
    audit_content_integrity,
    audit_cross_reference,
    audit_localization_coverage
)
from aem_mcp.datasets.engine import (
    materialize_dataset,
    get_dataset_metadata,
    get_dataset_rows,
    analyze_dataset,
    match_datasets,
    export_dataset,
    discard_dataset,
    cleanup_expired_datasets
)

cleanup_expired_datasets()

mcp = FastMCP(
    "AEM Content Intelligence",
    instructions=CANONICAL_PROMPT
)

# Simulator instance when in mock mode
_SIMULATOR: JcrEngine | None = None
if CONFIG.is_mock():
    _SIMULATOR = JcrEngine(CONFIG.mock_jcr_path)

_IMS_AUTH: AdobeImsAuthProvider | None = None
if CONFIG.mode == "cloud":
    _IMS_AUTH = AdobeImsAuthProvider()


# --- HTTP / Simulator Dispatcher ---

def _get_aem_json(path: str, params: dict[str, Any] | None = None) -> Any:
    if CONFIG.is_mock():
        assert _SIMULATOR is not None
        if path.startswith("/bin/querybuilder"):
            return _SIMULATOR.querybuilder(params or {})
        return _SIMULATOR.get_json(path)

    # Live Mode HTTP Request
    if not path.startswith("/"):
        raise ValueError("AEM path must begin with '/'")
    suffix = "" if path.endswith(".json") or "/bin/" in path else ".json"
    url = f"{CONFIG.aem_url}{path}{suffix}"
    if params:
        url += "?" + urllib.parse.urlencode(params)

    req = urllib.request.Request(url, method="GET")
    if CONFIG.mode == "cloud":
        if _IMS_AUTH is None:
            raise RuntimeError("IMS Auth provider is uninitialized.")
        for header, val in _IMS_AUTH.get_auth_headers().items():
            req.add_header(header, val)
    else:
        token = base64.b64encode(f"{CONFIG.username}:{CONFIG.password}".encode()).decode()
        req.add_header("Authorization", f"Basic {token}")

    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)



# --- MCP Resources ---

@mcp.resource("aem://prompt")
def aem_prompt_resource() -> str:
    """Canonical operating instructions for AEM Content Intelligence."""
    return CANONICAL_PROMPT


@mcp.resource("aem://runtime")
def aem_runtime_resource() -> str:
    """Safe active-environment runtime configuration."""
    return json.dumps(get_runtime_info(), indent=2)


# --- Master Entity Registry Tools ---

@mcp.tool()
def registry_indexes() -> str:
    """List available Master Entity Registry indexes (properties, brands, destinations)."""
    return json.dumps(get_registry_indexes(), indent=2)


@mcp.tool()
def registry_search(
    index: str,
    query: str = "",
    city: str = "",
    brand: str = "",
    limit: int = 50
) -> str:
    """Search Master Entity Registry records with structured filters."""
    res = search_registry(index=index, query=query, city=city, brand=brand, limit=limit)
    return json.dumps(res, ensure_ascii=False, indent=2)


@mcp.tool()
def registry_get(index: str, identifier: str) -> str:
    """Retrieve one canonical business entity record by primary identifier (e.g. hotel_id or brand_id)."""
    res = get_registry_entity(index=index, identifier=identifier)
    return json.dumps(res, ensure_ascii=False, indent=2)


# --- AEM Core Read-Only Tools ---

@mcp.tool()
def aem_json(path: str, depth: int = 0) -> str:
    """Read an AEM repository path as JSON using Sling selector conventions (depth=0..3)."""
    depth = min(max(int(depth), 0), 3)
    selector = f".{depth}" if depth else ""
    requested_path = path.removesuffix(".json") + selector

    if CONFIG.is_mock():
        assert _SIMULATOR is not None
        data = _SIMULATOR.get_json(requested_path)
    else:
        data = _get_aem_json(requested_path)

    return json.dumps({
        "mode": CONFIG.mode,
        "path": requested_path,
        "data": data
    }, ensure_ascii=False, indent=2)


@mcp.tool()
def aem_traverse(path: str, depth: int = 1) -> str:
    """Explore an AEM node and its immediate descendant levels (bounded up to depth 3)."""
    return aem_json(path=path, depth=min(max(depth, 1), 3))


@mcp.tool()
def aem_querybuilder(query: dict[str, str]) -> str:
    """Run a bounded, read-only AEM QueryBuilder query (path, type, fulltext, property, p.limit)."""
    if "path" not in query:
        raise ValueError("QueryBuilder query requires a 'path' parameter.")
    limit = int(query.get("p.limit", 20))
    if limit > 1000:
        raise ValueError("QueryBuilder p.limit must not exceed 1000.")

    start_time = time.perf_counter()
    data = _get_aem_json("/bin/querybuilder", query)
    duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
    return json.dumps({
        "mode": CONFIG.mode,
        "query": query,
        "execution_time_ms": duration_ms,
        "data": data
    }, ensure_ascii=False, indent=2)


@mcp.tool()
def aem_find_references(path: str) -> str:
    """Find repository paths (templates, models, fragments, assets) referenced by an AEM node."""
    if CONFIG.is_mock():
        assert _SIMULATOR is not None
        refs = _SIMULATOR.find_references(path)
    else:
        node_data = _get_aem_json(path)
        serialized = json.dumps(node_data, ensure_ascii=False)
        refs = sorted(set(
            re.findall(r"/(?:content|conf|etc|apps|libs)/[^\s\"\\,}]+", serialized)
        ))
        clean = path.removesuffix(".json").rstrip("/")
        refs = [r for r in refs if r != clean and not r.startswith(clean + "/")]

    return json.dumps({
        "source": path,
        "reference_count": len(refs),
        "references": refs
    }, indent=2)


@mcp.tool()
def aem_discover_properties(
    path: str,
    focus_term: str = "",
    node_type: str = "cq:Page",
    sample_limit: int = 25
) -> str:
    """Discover observed properties and values across representative child nodes under a root path."""
    sample_limit = min(max(int(sample_limit), 1), 50)
    query = {"path": path, "type": node_type, "p.limit": str(sample_limit)}
    if focus_term:
        query["fulltext"] = focus_term

    qb_result = _get_aem_json("/bin/querybuilder", query)
    hits = qb_result.get("hits", [])

    observed: dict[str, dict[str, Any]] = {}
    inspected = []

    for hit in hits:
        hit_path = hit.get("jcr:path") or hit.get("path")
        if not hit_path:
            continue
        try:
            node_json = _get_aem_json(f"{hit_path}.1.json")
        except Exception:
            continue

        inspected.append(hit_path)
        content = node_json.get("jcr:content", {}) if isinstance(node_json, dict) else {}
        for prop, val in content.items():
            if isinstance(val, (str, int, float, bool)):
                entry = observed.setdefault(prop, {"count": 0, "values": set()})
                entry["count"] += 1
                entry["values"].add(str(val))

    formatted = {
        k: {"observed_in_nodes": v["count"], "sample_values": sorted(list(v["values"]))[:10]}
        for k, v in observed.items()
    }

    return json.dumps({
        "path": path,
        "node_type": node_type,
        "inspected_nodes": len(inspected),
        "properties": formatted
    }, indent=2)


@mcp.tool()
def aem_query_dataset(
    query: dict[str, str],
    properties: list[str] | None = None,
    page_size: int = 500,
    max_records: int = 100000
) -> str:
    """Materialize a paginated QueryBuilder result into an in-memory SQLite dataset table."""
    if "path" not in query:
        raise ValueError("QueryBuilder query requires a 'path' parameter.")

    page_size = min(max(int(page_size), 1), 1000)
    max_records = min(max(int(max_records), 1), 100000)

    rows = []
    offset = 0
    pages = 0
    complete = True

    start_time = time.perf_counter()
    while len(rows) < max_records:
        page_query = {**query, "p.limit": str(page_size), "p.offset": str(offset)}
        res = _get_aem_json("/bin/querybuilder", page_query)
        hits = res.get("hits", [])
        pages += 1
        if not hits:
            break

        for hit in hits:
            if properties:
                projected = {p: hit.get(p) for p in properties}
                projected["jcr:path"] = hit.get("jcr:path", "")
                rows.append(projected)
            else:
                rows.append(hit)

        offset += len(hits)
        if len(hits) < page_size:
            break
        if pages >= 200:
            complete = False
            break

    ds_id = materialize_dataset(
        rows=rows,
        metadata={
            "source": "aem_querybuilder",
            "query": query,
            "properties": properties,
            "complete": complete,
        }
    )
    duration_ms = round((time.perf_counter() - start_time) * 1000, 2)

    return json.dumps({
        "dataset_id": ds_id,
        "row_count": len(rows),
        "pages_fetched": pages,
        "execution_time_ms": duration_ms,
        "complete": complete,
        "sample": rows[:5],
        "next": f"Use dataset_analyze(dataset_id='{ds_id}', operation='group_by', field='...') or dataset_export"
    }, ensure_ascii=False, indent=2)


# --- Dataset Analysis & Export Tools ---

@mcp.tool()
def dataset_info(dataset_id: str) -> str:
    """Retrieve metadata and column information for a materialized dataset."""
    meta = get_dataset_metadata(dataset_id)
    return json.dumps(meta, indent=2)


@mcp.tool()
def dataset_get_rows(dataset_id: str, offset: int = 0, limit: int = 100) -> str:
    """Read a bounded window of rows from a materialized dataset."""
    rows = get_dataset_rows(dataset_id=dataset_id, offset=offset, limit=limit)
    return json.dumps({"dataset_id": dataset_id, "offset": offset, "limit": limit, "rows": rows}, ensure_ascii=False, indent=2)


@mcp.tool()
def dataset_analyze(
    dataset_id: str,
    operation: str,
    field: str = "",
    value: str = "",
    other_field: str = "",
    operator: str = "equals"
) -> str:
    """Run server-side aggregations (count, distinct, missing, group_by, filter) over a dataset."""
    res = analyze_dataset(
        dataset_id=dataset_id,
        operation=operation,
        field=field,
        value=value,
        other_field=other_field,
        operator=operator
    )
    return json.dumps(res, ensure_ascii=False, indent=2)


@mcp.tool()
def dataset_match(
    left_dataset_id: str,
    right_dataset_id: str,
    left_field: str,
    right_field: str,
    match_type: str = "exact"
) -> str:
    """Reconcile two datasets on a key (e.g. AEM content vs Property Master) and highlight discrepancies."""
    res = match_datasets(
        left_dataset_id=left_dataset_id,
        right_dataset_id=right_dataset_id,
        left_field=left_field,
        right_field=right_field,
        match_type=match_type
    )
    return json.dumps(res, ensure_ascii=False, indent=2)


@mcp.tool()
def dataset_export(dataset_id: str, format: str = "csv") -> str:
    """Generate a downloadable CSV or Excel (.xlsx) export artifact with a local HTTP download link."""
    res = export_dataset(dataset_id=dataset_id, fmt=format)
    return json.dumps(res, indent=2)


@mcp.tool()
def dataset_discard(dataset_id: str) -> str:
    """Delete a temporary materialized dataset and free storage."""
    deleted = discard_dataset(dataset_id)
    return json.dumps({"dataset_id": dataset_id, "deleted": deleted})


# --- Content & Discrepancy Auditing Tools ---

@mcp.tool()
def aem_audit_content_integrity(
    root_path: str = "/content/novaria",
    check_assets: bool = True,
    check_promos: bool = True,
    check_fragments: bool = True,
    limit: int = 100
) -> str:
    """Audit AEM pages under root_path for broken DAM assets, expired campaigns, and incomplete content fragments."""
    all_nodes = _SIMULATOR.all_nodes if _SIMULATOR is not None else None
    res = audit_content_integrity(
        node_getter=lambda p: _get_aem_json(p),
        root_path=root_path,
        check_assets=check_assets,
        check_promos=check_promos,
        check_fragments=check_fragments,
        limit=limit,
        all_nodes=all_nodes
    )
    return json.dumps(res, ensure_ascii=False, indent=2)


@mcp.tool()
def aem_audit_cross_reference(
    path: str = "/content/novaria/us/en/hotels",
    limit: int = 100
) -> str:
    """Cross-reference AEM authored properties against Canonical Master Entity Registry (PMS database) for ratings, status, amenities, and orphans."""
    all_nodes = _SIMULATOR.all_nodes if _SIMULATOR is not None else None
    res = audit_cross_reference(
        node_getter=lambda p: _get_aem_json(p),
        path=path,
        limit=limit,
        all_nodes=all_nodes
    )
    return json.dumps(res, ensure_ascii=False, indent=2)


@mcp.tool()
def aem_audit_localization_coverage(
    base_locale: str = "us/en",
    target_locales: list[str] | None = None,
    subpath: str = "hotels",
    limit: int = 50
) -> str:
    """Audit multi-region localization coverage comparing canonical base locale against target regional sites (e.g. fr/fr, de/de, jp/ja)."""
    all_nodes = _SIMULATOR.all_nodes if _SIMULATOR is not None else {}
    res = audit_localization_coverage(
        all_nodes=all_nodes,
        base_locale=base_locale,
        target_locales=target_locales,
        subpath=subpath,
        limit=limit
    )
    return json.dumps(res, ensure_ascii=False, indent=2)


@mcp.tool()
def aem_compile_querybuilder_sql2(query: dict[str, str]) -> str:
    """Compile an AEM QueryBuilder predicate map into an optimized Jackrabbit Oak JCR-SQL2 query for index analysis."""
    try:
        from sling_querybuilder import QueryBuilderCompiler
        compiled = QueryBuilderCompiler(query).compile()
        sql2 = compiled.sql2
        provider = "sling-querybuilder"
    except ImportError:
        p = query.get("path", "/content")
        t = query.get("type", "nt:base")
        clauses = [f"ISDESCENDANTNODE(n, '{p}')"]
        if "property" in query and "property.value" in query:
            prop = query["property"]
            val = query["property.value"]
            clauses.append(f"n.[{prop}] = '{val}'")
        where = " AND ".join(clauses)
        sql2 = f"SELECT * FROM [{t}] AS n WHERE {where}"
        provider = "builtin_basic_compiler"

    return json.dumps({
        "compiler": provider,
        "querybuilder": query,
        "jcr_sql2": sql2
    }, indent=2)


# --- Sandboxed In-Memory Analysis & Feedback ---

_CODE_BLOCKLIST = {
    "os", "pathlib", "subprocess", "socket", "urllib", "http", "ftplib",
    "shutil", "tempfile", "ctypes", "multiprocessing", "importlib",
}
_CALL_BLOCKLIST = {"open", "exec", "eval", "compile", "input", "__import__"}


@mcp.tool()
def execute_code(code: str, timeout_seconds: int = 120) -> str:
    """Run bounded in-memory Python calculations over retrieved data. Filesystem and network are strictly blocked."""
    tree = ast.parse(code, mode="exec")
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [alias.name.split(".", 1)[0] for alias in node.names]
            if any(name in _CODE_BLOCKLIST for name in names):
                raise ValueError("execute_code cannot import filesystem, network, or OS modules.")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in _CALL_BLOCKLIST:
                raise ValueError(f"Function call '{node.func.id}' is blocked.")

    local_scope: dict[str, Any] = {}
    exec(code, {"__builtins__": {k: v for k, v in __builtins__.items() if k not in _CALL_BLOCKLIST}}, local_scope)
    
    # Strip complex non-serializable objects
    sanitized = {k: v for k, v in local_scope.items() if isinstance(v, (int, float, str, bool, list, dict, type(None)))}
    return json.dumps(sanitized, ensure_ascii=False, indent=2)


@mcp.tool()
def submit_tool_recommendation(
    task_summary: str,
    missing_capability: str,
    rationale: str,
    proposed_tool: str = "",
    priority: str = "normal",
) -> str:
    """Submit a structured suggestion for a new or improved MCP capability."""
    rec_file = ROOT_DIR / "feedback" / "recommendations.jsonl"
    rec_file.parent.mkdir(exist_ok=True, parents=True)
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "task_summary": task_summary,
        "missing_capability": missing_capability,
        "rationale": rationale,
        "proposed_tool": proposed_tool,
        "priority": priority,
    }
    with open(rec_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return json.dumps({"submitted": True, "status": "recorded"})


def main():
    mcp.run()


if __name__ == "__main__":
    main()
