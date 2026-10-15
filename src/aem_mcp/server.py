"""
AEM Content Intelligence MCP Server.

A production-grade Model Context Protocol (MCP) server for Adobe Experience Manager (AEM).
Provides read-only access to AEM JCR, QueryBuilder, and Master Entity Registry with
in-memory SQLite dataset materialization, server-side analytics, and Excel/CSV export.
"""

import ast
import builtins
import concurrent.futures
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
from aem_mcp.prompts import CANONICAL_PROMPT, get_runtime_info, get_canonical_prompt
from aem_mcp.registry import get_registry_indexes, search_registry, get_registry_entity
from aem_mcp.simulator.jcr_engine import JcrEngine
from aem_mcp.audit import (
    audit_content_integrity,
    audit_cross_reference,
    audit_localization_coverage
)
from aem_mcp.datasets.engine import (
    StreamingDatasetBuilder,
    materialize_dataset,
    get_dataset_metadata,
    get_dataset_rows,
    analyze_dataset,
    match_datasets,
    export_dataset,
    discard_dataset,
    cleanup_expired_datasets,
    import_dataset_file
)

cleanup_expired_datasets()

mcp = FastMCP(
    "AEM Content Intelligence",
    instructions=CANONICAL_PROMPT
)

from aem_mcp.domains.manager import DOMAIN_MANAGER

# Simulator instance when in mock mode
_SIMULATOR: JcrEngine | None = None

def _reload_simulator_for_active_domain():
    global _SIMULATOR
    if CONFIG.is_mock():
        profile = DOMAIN_MANAGER.active_profile
        mock_path = profile.jcr_mock_path or CONFIG.mock_jcr_path
        _SIMULATOR = JcrEngine(mock_path)

_reload_simulator_for_active_domain()
DOMAIN_MANAGER.register_switch_listener(lambda _: _reload_simulator_for_active_domain())

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
    """Canonical operating instructions for AEM Content Intelligence reflecting active domain and mode."""
    if CONFIG.is_pure_aem():
        return get_canonical_prompt(
            root_path="/content",
            is_pure_aem=True
        )
    p = DOMAIN_MANAGER.active_profile
    return get_canonical_prompt(
        domain_name=p.name,
        root_path=p.root_path,
        primary_entity=p.primary_entity,
        is_pure_aem=False
    )


@mcp.resource("aem://runtime")
def aem_runtime_resource() -> str:
    """Safe active-environment runtime configuration."""
    info = get_runtime_info()
    info["active_instance"] = _ACTIVE_SCOPING.get("active_instance", "default-unscoped")
    info["role_preset"] = _ACTIVE_SCOPING.get("role_preset", "full")
    env_meta = _ACTIVE_SCOPING.get("environment")
    if isinstance(env_meta, dict) and env_meta.get("id"):
        info["environment_tier"] = str(env_meta["id"])
    info["authorized_tools_count"] = str(len(_REGISTERED_TOOL_NAMES))
    if not CONFIG.is_pure_aem():
        p = DOMAIN_MANAGER.active_profile
        info.update({
            "domain_id": p.id,
            "domain_name": p.name,
            "root_path": p.root_path,
            "primary_entity": p.primary_entity,
            "data_source_type": p.data_source_type,
            "data_source_file": p.data_source_file,
            "audit_rules_count": str(len(p.audit_rules)),
        })
    else:
        info.update({
            "pure_aem_mode": "True",
            "root_path": "/content",
            "description": "Operating in Pure AEM Content Intelligence & Oak Indexing mode."
        })
    return json.dumps(info, indent=2)


@mcp.resource("aem://environment")
def aem_environment_resource() -> str:
    """Identifies the active MCP instance and the single AEM environment it is bound to."""
    env_meta = _ACTIVE_SCOPING.get("environment") or {}
    env_id = str(env_meta.get("id") or os.getenv("AEM_MCP_ENV") or "dev")
    pkg_tools = [t for t in _REGISTERED_TOOL_NAMES if t.startswith("package_")]
    payload = {
        "active_instance": _ACTIVE_SCOPING.get("active_instance", "default-unscoped"),
        "role_preset": _ACTIVE_SCOPING.get("role_preset", "full"),
        "environment_id": env_id,
        "environment_name": env_meta.get("name", f"{env_id.upper()} Environment"),
        "tier": env_meta.get("tier", env_id),
        "mode": CONFIG.mode,
        "aem_url": CONFIG.aem_url if not CONFIG.is_mock() else "virtual://jcr-simulator",
        "authorized_package_tools": pkg_tools,
        "package_exchange_mode": "opaque_handle_private_storage",
        "note": "Each MCP process is bound to one AEM environment. Package operations act only on this instance's environment.",
    }
    return json.dumps(payload, indent=2)


# --- Master Entity Registry Tools (Registered conditionally when CONFIG.enable_registry is True) ---

def registry_indexes() -> str:
    """List available Master Entity Registry indexes (properties, brands, destinations)."""
    return json.dumps(get_registry_indexes(), indent=2)


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


def registry_get(index: str, identifier: str) -> str:
    """Retrieve one canonical business entity record by primary identifier (e.g. hotel_id or brand_id)."""
    res = get_registry_entity(index=index, identifier=identifier)
    return json.dumps(res, ensure_ascii=False, indent=2)


def registry_query_dataset(
    index: str,
    query: str = "",
    city: str = "",
    brand: str = "",
    max_records: int = 100000
) -> str:
    """
    Materialize records from the active Master Entity Registry into an on-disk SQLite dataset table.
    Enables cross-system joins (dataset_match) between canonical enterprise data and AEM content.
    Supports any active domain format (SQLite, CSV, JSON, or external enterprise databases).
    """
    res = search_registry(index=index, query=query, city=city, brand=brand, limit=max_records)
    records = res.get("results", [])
    ds_id = materialize_dataset(
        rows=records,
        metadata={
            "source": f"registry_{index}",
            "index": index,
            "query": query,
            "domain": DOMAIN_MANAGER.active_domain_id,
        }
    )
    return json.dumps({
        "dataset_id": ds_id,
        "row_count": len(records),
        "index": index,
        "domain": DOMAIN_MANAGER.active_domain_id,
        "sample": records[:5],
        "next": f"Use dataset_match(left_dataset_id='<aem_dataset_id>', right_dataset_id='{ds_id}', left_field='...', right_field='...')"
    }, ensure_ascii=False, indent=2)


# --- AEM Core Read-Only Tools ---

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


def aem_traverse(path: str, depth: int = 1) -> str:
    """Explore an AEM node and its immediate descendant levels (bounded up to depth 3)."""
    return aem_json(path=path, depth=min(max(depth, 1), 3))


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


def aem_query_dataset(
    query: dict[str, str],
    properties: list[str] | None = None,
    page_size: int = 500,
    max_records: int = 100000
) -> str:
    """
    Stream and materialize paginated QueryBuilder results directly into an on-disk SQLite dataset.
    Maintains bounded memory consumption (~2-5MB) regardless of repository size.
    """
    if "path" not in query:
        raise ValueError("QueryBuilder query requires a 'path' parameter.")

    page_size = min(max(int(page_size), 1), 1000)
    max_limit = int(max_records) if int(max_records) > 0 else None

    offset = 0
    pages = 0
    complete = True
    sample_rows: list[dict[str, Any]] = []

    start_time = time.perf_counter()
    builder = StreamingDatasetBuilder(
        metadata={
            "source": "aem_querybuilder",
            "query": query,
            "properties": properties,
        }
    )
    try:
        while True:
            remaining = (max_limit - builder.row_count) if max_limit is not None else page_size
            if remaining <= 0:
                complete = False
                break

            current_page_size = min(page_size, remaining)
            page_query = {**query, "p.limit": str(current_page_size), "p.offset": str(offset)}
            res = _get_aem_json("/bin/querybuilder", page_query)
            hits = res.get("hits", [])
            pages += 1
            if not hits:
                break

            page_rows = []
            for hit in hits:
                if properties:
                    projected = {p: hit.get(p) for p in properties}
                    projected["jcr:path"] = hit.get("jcr:path", "")
                    page_rows.append(projected)
                else:
                    page_rows.append(hit)

            if len(sample_rows) < 5:
                sample_rows.extend(page_rows[:5 - len(sample_rows)])

            builder.append_batch(page_rows)
            offset += len(hits)

            if len(hits) < current_page_size:
                break

        ds_id = builder.close(extra_metadata={"complete": complete, "pages_fetched": pages})
    except Exception:
        builder.close()
        raise

    duration_ms = round((time.perf_counter() - start_time) * 1000, 2)

    return json.dumps({
        "dataset_id": ds_id,
        "row_count": builder.row_count,
        "pages_fetched": pages,
        "execution_time_ms": duration_ms,
        "complete": complete,
        "sample": sample_rows,
        "next": f"Use dataset_analyze(dataset_id='{ds_id}', operation='group_by', field='...') or dataset_export"
    }, ensure_ascii=False, indent=2)


# --- Dataset Analysis & Export Tools ---

def dataset_info(dataset_id: str) -> str:
    """Retrieve metadata and column information for a materialized dataset."""
    meta = get_dataset_metadata(dataset_id)
    return json.dumps(meta, indent=2)


def dataset_get_rows(dataset_id: str, offset: int = 0, limit: int = 100) -> str:
    """Read a bounded window of rows from a materialized dataset."""
    rows = get_dataset_rows(dataset_id=dataset_id, offset=offset, limit=limit)
    return json.dumps({"dataset_id": dataset_id, "offset": offset, "limit": limit, "rows": rows}, ensure_ascii=False, indent=2)


def dataset_analyze(
    dataset_id: str,
    operation: str,
    field: str = "",
    value: str = "",
    other_field: str = "",
    operator: str = "equals"
) -> str:
    """
    Run server-side aggregations and analytics over a materialized dataset at native SQLite C-speed.
    
    Operations:
      - 'count': Match count & percentage. Supports operators ('equals', 'unequals', 'contains', 'starts_with', 'ends_with', 'like', 'exists', 'not', 'gt', 'gte', 'lt', 'lte', 'between', 'before', 'after').
      - 'missing': Completeness audit (missing_count, present_count, completeness_pct).
      - 'group_by': Categorical breakdown and distinct frequency distribution.
      - 'stats': Numeric statistical summary (min, max, avg, sum, numeric_rows).
      - 'avg', 'min', 'max', 'sum': Direct numeric scalar aggregations.
      - 'filter': Bounded sample extraction matching operator conditions.
    """
    res = analyze_dataset(
        dataset_id=dataset_id,
        operation=operation,
        field=field,
        value=value,
        other_field=other_field,
        operator=operator
    )
    return json.dumps(res, ensure_ascii=False, indent=2)


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


def dataset_export(dataset_id: str, format: str = "csv") -> str:
    """Generate a downloadable CSV or Excel (.xlsx) export artifact with a local HTTP download link."""
    res = export_dataset(dataset_id=dataset_id, fmt=format)
    return json.dumps(res, indent=2)


def dataset_discard(dataset_id: str) -> str:
    """Delete a temporary materialized dataset and free storage."""
    deleted = discard_dataset(dataset_id)
    return json.dumps({"dataset_id": dataset_id, "deleted": deleted})


def dataset_import(file_path: str, format: str = "auto", name: str = "") -> str:
    """
    Import an external structured file (CSV, JSON array, or JSON Lines) directly into an on-disk SQLite dataset table.
    Enables external enterprise MCPs (Snowflake, Postgres, SAP, Salsify) to pass data for
    cross-system matching with AEM content without dumping raw rows into the LLM context window.
    """
    res = import_dataset_file(file_path=file_path, fmt=format, name=name)
    return json.dumps(res, ensure_ascii=False, indent=2)


class _LiveJcrAdapter:
    """Adapter presenting a JcrEngine-compatible interface over live AEM HTTP endpoints."""
    def querybuilder(self, params: dict[str, Any]) -> dict[str, Any]:
        return _get_aem_json("/bin/querybuilder.json", params)

    def get_json(self, path: str, depth: int = 0) -> Any:
        if depth <= 0 or path.endswith(".json"):
            return _get_aem_json(path)
        return _get_aem_json(f"{path}.{depth}.json")


# --- Content & Discrepancy Auditing Tools ---

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
        all_nodes=all_nodes,
        query_runner=lambda q: _get_aem_json("/bin/querybuilder.json", q),
    )
    return json.dumps(res, ensure_ascii=False, indent=2)


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
        all_nodes=all_nodes,
        query_runner=lambda q: _get_aem_json("/bin/querybuilder.json", q),
    )
    return json.dumps(res, ensure_ascii=False, indent=2)


def aem_audit_localization_coverage(
    base_locale: str = "us/en",
    target_locales: list[str] | None = None,
    subpath: str = "hotels",
    limit: int = 50
) -> str:
    """Audit multi-region localization coverage comparing canonical base locale against target regional sites (e.g. fr/fr, de/de, jp/ja)."""
    all_nodes = _SIMULATOR.all_nodes if _SIMULATOR is not None else None
    res = audit_localization_coverage(
        all_nodes=all_nodes,
        base_locale=base_locale,
        target_locales=target_locales,
        subpath=subpath,
        limit=limit,
        query_runner=lambda q: _get_aem_json("/bin/querybuilder.json", q),
    )
    return json.dumps(res, ensure_ascii=False, indent=2)


def aem_compile_querybuilder_sql2(query: dict[str, str]) -> str:
    """Compile an AEM QueryBuilder predicate map into an optimized Jackrabbit Oak JCR-SQL2 query for index analysis."""
    try:
        from sling_querybuilder import QueryBuilderCompiler
        compiled = QueryBuilderCompiler(query).compile()
        sql2 = compiled.sql2
        provider = "sling-querybuilder"
    except ImportError:
        p = query.get("path", "/content").replace("'", "''")
        t = query.get("type", "nt:base").replace("]", "")
        clauses = [f"ISDESCENDANTNODE(n, '{p}')"]
        if "property" in query and "property.value" in query:
            prop = query["property"].replace("]", "")
            val = str(query["property.value"]).replace("'", "''")
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
    "shutil", "tempfile", "ctypes", "multiprocessing", "importlib", "sys",
    "builtins", "inspect", "gc",
}
_CALL_BLOCKLIST = {
    "open", "exec", "eval", "compile", "input", "__import__",
    "getattr", "setattr", "delattr", "globals", "locals", "vars", "dir", "breakpoint",
}


def execute_code(code: str, timeout_seconds: int = 120) -> str:
    """Run bounded in-memory Python calculations over retrieved data. Filesystem, network, dunder introspection, and unbounded loops are strictly blocked."""
    tree = ast.parse(code, mode="exec")
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [alias.name.split(".", 1)[0] for alias in node.names]
            if any(name in _CODE_BLOCKLIST for name in names):
                raise ValueError("execute_code cannot import filesystem, network, or OS modules.")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in _CALL_BLOCKLIST:
                raise ValueError(f"Function call '{node.func.id}' is blocked.")
        if isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            raise ValueError(f"Access to dunder attribute '{node.attr}' is blocked.")
        if isinstance(node, ast.Name) and node.id.startswith("__"):
            raise ValueError(f"Access to dunder identifier '{node.id}' is blocked.")

    safe_builtins = {
        k: v for k, v in vars(builtins).items()
        if k not in _CALL_BLOCKLIST and not k.startswith("__")
    }
    local_scope: dict[str, Any] = {}
    bounded_timeout = min(max(int(timeout_seconds), 1), 120)
    exec_error: list[BaseException] = []

    def _run_sandbox() -> None:
        try:
            exec(code, {"__builtins__": safe_builtins}, local_scope)
        except BaseException as exc:
            exec_error.append(exc)

    import threading
    worker = threading.Thread(target=_run_sandbox, daemon=True)
    worker.start()
    worker.join(timeout=bounded_timeout)
    if worker.is_alive():
        raise TimeoutError(f"execute_code timed out after {bounded_timeout} seconds.")
    if exec_error:
        raise exec_error[0]

    # Strip complex non-serializable objects
    sanitized = {k: v for k, v in local_scope.items() if isinstance(v, (int, float, str, bool, list, dict, type(None)))}
    return json.dumps(sanitized, ensure_ascii=False, indent=2)


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


def aem_lint_query_indexing(query: dict[str, Any]) -> str:
    """
    Lint an AEM QueryBuilder query for Apache Jackrabbit Oak indexing performance.
    Detects unindexed property traversals, predicts Oak query plans, and generates
    production-ready /oak:index definitions (JSON, FileVault XML & OSGi Repoinit) to prevent query timeouts.
    """
    search_path = query.get("path", "/content")
    node_type = query.get("type", "nt:base")
    fulltext = query.get("fulltext")

    # Extract all filtered properties
    filtered_props = []
    if "property" in query:
        filtered_props.append(str(query["property"]))

    for k, v in query.items():
        if re.match(r"^(\d+_)?property$", k) and k != "property":
            filtered_props.append(str(v))
        elif k in ("tagid", "1_tagid", "2_tagid") or k.endswith("_tagid"):
            tag_prop = query.get("tagid.property", "jcr:content/cq:tags")
            filtered_props.append(str(tag_prop))
        elif k == "daterange.property":
            filtered_props.append(str(v))

    # Clean property names
    filtered_props = list(dict.fromkeys(p.strip().lstrip("@") for p in filtered_props if p))

    # Standard default Oak indexes
    standard_indexed = {"jcr:primaryType", "jcr:uuid"}
    unindexed = [p for p in filtered_props if p not in standard_indexed]

    has_fulltext = bool(fulltext)
    is_traversal = bool(unindexed) and not has_fulltext
    risk_level = "CRITICAL" if is_traversal else ("OPTIMAL" if not unindexed else "WARNING")

    # Build simulated Oak plan
    if has_fulltext:
        plan = f"[{node_type}] as [n] /* lucene:lucene(/oak:index/lucene) +:ancestors:{search_path} +fulltext:{fulltext} */"
        diagnosis = "Query utilizes the out-of-the-box Oak Lucene fulltext index (/oak:index/lucene)."
        advice = "Query execution is index-backed and optimal."
    elif is_traversal:
        plan = f"[{node_type}] as [n] /* traverse \"{search_path}//*\" where unindexed: {unindexed} */"
        diagnosis = (
            f"Query filters on custom properties {unindexed} without a covering Oak index under '{search_path}'. "
            "Jackrabbit Oak will perform an unindexed repository traversal. If the path contains >10,000 nodes, "
            "Oak will abort the query with an 'org.apache.jackrabbit.oak.query.FilterIterators: scan limit exceeded' exception."
        )
        advice = f"Define a custom Oak PropertyIndex or LuceneIndex for {unindexed} to ensure sub-millisecond lookups."
    else:
        plan = f"[{node_type}] as [n] /* property:nodetype(/oak:index/nodetype) where [n].[jcr:primaryType] = '{node_type}' */"
        diagnosis = "Query matches node type using default Oak nodetype index."
        advice = "Query execution is index-backed."

    # Build recommended index definitions if traversal detected
    remediations = {}
    if unindexed:
        first_prop = unindexed[0].replace("jcr:content/", "").replace("/", "_").replace(":", "_")
        idx_name = f"{first_prop}Index"
        
        # Property index
        prop_idx_json = {
            "jcr:primaryType": "oak:QueryIndexDefinition",
            "type": "property",
            "propertyNames": unindexed,
            "declaringNodeTypes": [node_type] if node_type != "nt:base" else [],
            "reindex": True
        }
        
        # Lucene index
        lucene_rule_props = {}
        for p in unindexed:
            cname = p.replace(":", "_").replace("/", "_")
            lucene_rule_props[cname] = {
                "jcr:primaryType": "nt:unstructured",
                "name": p,
                "propertyIndex": True,
                "ordered": True
            }

        lucene_idx_json = {
            "jcr:primaryType": "oak:QueryIndexDefinition",
            "type": "lucene",
            "async": ["async", "nrt"],
            "compatVersion": 2,
            "evaluatePathRestrictions": True,
            "reindex": True,
            "indexRules": {
                "jcr:primaryType": "nt:unstructured",
                node_type: {
                    "jcr:primaryType": "nt:unstructured",
                    "properties": {
                        "jcr:primaryType": "nt:unstructured",
                        **lucene_rule_props
                    }
                }
            }
        }

        quoted_props = ", ".join(f'"{p}"' for p in unindexed)
        repoinit_lines = [
            f"create path /oak:index/{idx_name}(oak:QueryIndexDefinition)",
            f"set properties on /oak:index/{idx_name}",
            '  set type to "property"',
            f"  set propertyNames{{Name}} to {quoted_props}",
        ]
        if node_type != "nt:base":
            repoinit_lines.append(f'  set declaringNodeTypes{{Name}} to "{node_type}"')
        repoinit_lines.extend([
            "  set reindex{Boolean} to true",
            "end",
        ])

        remediations["property_index"] = {
            "path": f"/oak:index/{idx_name}",
            "json": prop_idx_json,
            "filevault_xml": (
                f'<?xml version="1.0" encoding="UTF-8"?>\n'
                f'<jcr:root xmlns:jcr="http://www.jcp.org/jcr/1.0" xmlns:oak="http://jackrabbit.apache.org/oak/ns/1.0"\n'
                f'    jcr:primaryType="oak:QueryIndexDefinition"\n'
                f'    type="property"\n'
                f'    reindex="{{Boolean}}true"\n'
                f'    propertyNames="[{",".join(unindexed)}]"/>'
            ),
            "repoinit": "\n".join(repoinit_lines),
        }
        remediations["lucene_index"] = {
            "path": f"/oak:index/{idx_name}Lucene",
            "json": lucene_idx_json
        }

    report = {
        "search_path": search_path,
        "node_type": node_type,
        "risk_level": risk_level,
        "is_traversal": is_traversal,
        "filtered_properties": filtered_props,
        "unindexed_properties": unindexed,
        "simulated_oak_plan": plan,
        "diagnosis": diagnosis,
        "advice": advice,
        "recommended_indexes": remediations
    }
    return json.dumps(report, ensure_ascii=False, indent=2)


# --- Multi-Domain Management & Governance Tools (Registered conditionally when CONFIG.enable_registry is True) ---

def domain_list() -> str:
    """
    List available enterprise domain profiles (Hospitality, Automotive, Retail)
    and the currently active domain profile.
    """
    profiles = []
    for d_id, p in DOMAIN_MANAGER.profiles.items():
        profiles.append({
            "id": d_id,
            "name": p.name,
            "description": p.description,
            "primary_entity": p.primary_entity,
            "data_source_type": p.data_source_type,
            "data_source_file": p.data_source_file,
            "root_path": p.root_path,
            "is_active": (d_id == DOMAIN_MANAGER.active_domain_id),
            "audit_rules_count": len(p.audit_rules)
        })
    return json.dumps({
        "active_domain": DOMAIN_MANAGER.active_domain_id,
        "total_domains": len(profiles),
        "domains": profiles
    }, indent=2)


def domain_switch(name: str) -> str:
    """
    Switch the active enterprise domain profile (e.g. 'hospitality', 'automotive', 'retail').
    Dynamically switches the Master Registry (SQLite, CSV, JSON) and JCR mock content store.
    """
    try:
        profile = DOMAIN_MANAGER.switch_domain(name)
        _reload_simulator_for_active_domain()
        return json.dumps({
            "success": True,
            "switched_to": profile.id,
            "name": profile.name,
            "primary_entity": profile.primary_entity,
            "data_source_type": profile.data_source_type,
            "data_source_file": profile.data_source_file,
            "root_path": profile.root_path,
            "audit_rules_count": len(profile.audit_rules)
        }, indent=2)
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)}, indent=2)


def domain_get_active() -> str:
    """
    Get detailed information about the active enterprise domain profile,
    including table schemas, JCR field mappings, and registered audit rules.
    """
    p = DOMAIN_MANAGER.active_profile
    return json.dumps({
        "id": p.id,
        "name": p.name,
        "description": p.description,
        "primary_entity": p.primary_entity,
        "data_source_type": p.data_source_type,
        "data_source_file": p.data_source_file,
        "root_path": p.root_path,
        "tables": p.table_descriptions,
        "field_mappings": p.field_mappings,
        "audit_rules": [
            {
                "id": r.id,
                "name": r.name,
                "severity": r.severity,
                "rule_type": r.rule_type,
                "condition": r.condition,
                "message": r.message
            }
            for r in p.audit_rules
        ]
    }, indent=2)


def aem_audit_domain_rules(domain: str = "", limit: int = 100) -> str:
    """
    Executes declarative domain governance rules for the active (or specified) domain,
    reconciling AEM authored content against the canonical master data source (SQLite, CSV, JSON).
    Works in both offline simulator mode and live AEM (onprem / cloud) mode.
    """
    if domain and domain.lower().strip() != DOMAIN_MANAGER.active_domain_id:
        domain_switch(domain)

    engine = _SIMULATOR if _SIMULATOR is not None else _LiveJcrAdapter()
    findings = DOMAIN_MANAGER.evaluate_audit_rules(engine, limit=limit)
    return json.dumps(findings, indent=2)


# --- Environment-Scoped CRX Package Manager & Multi-Instance Sync Tools ---

from aem_mcp.packages import PACKAGE_MANAGER


def _current_bound_env_id() -> str:
    env_meta = _ACTIVE_SCOPING.get("environment")
    if isinstance(env_meta, dict) and env_meta.get("id"):
        return str(env_meta["id"])
    return (
        os.getenv("AEM_MCP_ENV", "").strip()
        or _ACTIVE_SCOPING.get("active_instance", "")
        or "dev"
    )


def package_list(group: str = "") -> str:
    """
    Read-only: List CRX packages in the active MCP instance's AEM environment.
    Does not accept a target environment parameter; operates strictly on the bound instance environment.
    """
    res = PACKAGE_MANAGER.list_packages(env_id=_current_bound_env_id(), group=group)
    return json.dumps(res, ensure_ascii=False, indent=2)


def package_create(
    name: str,
    group: str = "my_packages",
    version: str = "1.0",
    filters: list[str] | None = None,
    confirm: bool = False,
) -> str:
    """
    Create an empty CRX package definition in the active environment and optionally configure repository filter roots.
    Requires explicit confirmation (confirm=True).
    Enforces guardrails: 100 MB size limit, 10,000 node limit, allowed root types (/content, /conf, /apps,
    /libs, /etc/designs, /var/workflow/models), and minimum path depth ceiling.
    """
    res = PACKAGE_MANAGER.create_package(
        name=name,
        group=group,
        version=version,
        filters=filters,
        confirm=confirm,
        env_id=_current_bound_env_id(),
        simulator=_SIMULATOR,
        ims_auth=_IMS_AUTH,
    )
    return json.dumps(res, ensure_ascii=False, indent=2)


def package_set_filters(
    name: str,
    filters: list[str],
    group: str = "my_packages",
    version: str = "1.0",
    confirm: bool = False,
) -> str:
    """
    Replace repository filter roots on an existing CRX package definition via /crx/packmgr/update.jsp.
    Validates allowed root types (/content, /conf, /apps, /libs, /etc/designs, /var/workflow/models),
    minimum path depth ceiling, and 10,000 node limit.
    Requires explicit confirmation (confirm=True).
    """
    res = PACKAGE_MANAGER.set_filters(
        name=name,
        filters=filters,
        group=group,
        version=version,
        confirm=confirm,
        env_id=_current_bound_env_id(),
        simulator=_SIMULATOR,
        ims_auth=_IMS_AUTH,
    )
    return json.dumps(res, ensure_ascii=False, indent=2)


def package_build(
    name: str,
    group: str = "my_packages",
    version: str = "1.0",
    confirm: bool = False,
) -> str:
    """
    Build/assemble an existing CRX package in the active environment.
    Enforces 100 MB maximum archive size, 10,000 maximum JCR nodes, and filter path guardrails.
    Requires explicit confirmation (confirm=True).
    """
    res = PACKAGE_MANAGER.build_package(
        name=name,
        group=group,
        version=version,
        confirm=confirm,
        env_id=_current_bound_env_id(),
        simulator=_SIMULATOR,
        ims_auth=_IMS_AUTH,
    )
    return json.dumps(res, ensure_ascii=False, indent=2)


def package_download(
    name: str,
    group: str = "my_packages",
    version: str = "1.0",
) -> str:
    """
    Read-only: Download a built CRX package from the active environment into MCP-managed private exchange storage
    and return an opaque package_handle (pkg_<12hex>). Package bytes are never loaded into model context.
    Enforces 100 MB size limit, 10,000 node limit, and filter path depth/root type guardrails.
    """
    res = PACKAGE_MANAGER.download_package(
        name=name,
        group=group,
        version=version,
        env_id=_current_bound_env_id(),
        ims_auth=_IMS_AUTH,
    )
    return json.dumps(res, ensure_ascii=False, indent=2)


def package_upload(
    package_handle: str,
    force: bool = True,
    confirm: bool = False,
) -> str:
    """
    Upload a package from MCP-managed private exchange storage (by opaque package_handle)
    into the active MCP instance's AEM environment.
    Enforces 100 MB size limit, 10,000 node limit, and filter path depth/root type guardrails.
    Requires explicit confirmation (confirm=True).
    """
    res = PACKAGE_MANAGER.upload_package(
        package_handle=package_handle,
        force=force,
        confirm=confirm,
        env_id=_current_bound_env_id(),
        ims_auth=_IMS_AUTH,
    )
    return json.dumps(res, ensure_ascii=False, indent=2)


def package_install(
    name: str,
    group: str = "my_packages",
    version: str = "1.0",
    confirm: bool = False,
) -> str:
    """
    Install an uploaded or built CRX package in the active MCP instance's AEM environment.
    CRITICAL GUARDRAIL: Never install into '/libs' (prevents overwriting immutable AEM product code).
    Enforces 100 MB size limit, 10,000 node limit, and path depth/root type restrictions before installation.
    Requires explicit confirmation (confirm=True).
    """
    res = PACKAGE_MANAGER.install_package(
        name=name,
        group=group,
        version=version,
        confirm=confirm,
        env_id=_current_bound_env_id(),
        simulator=_SIMULATOR,
        ims_auth=_IMS_AUTH,
    )
    return json.dumps(res, ensure_ascii=False, indent=2)


# --- Instance, Role & Environment Scoping & Dynamic Tool Registration ---

import argparse
import sys
from aem_mcp.configurator import CONFIGURATOR_STORE, run_configurator_server

_CORE_SERVER_TOOLS = [
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
    dataset_import,
    aem_audit_content_integrity,
    aem_audit_cross_reference,
    aem_audit_localization_coverage,
    aem_compile_querybuilder_sql2,
    package_list,
    package_create,
    package_set_filters,
    package_build,
    package_download,
    package_upload,
    package_install,
    execute_code,
    submit_tool_recommendation,
    aem_lint_query_indexing,
]

_REGISTRY_AND_DOMAIN_TOOLS = [
    registry_indexes,
    registry_search,
    registry_get,
    registry_query_dataset,
    domain_list,
    domain_switch,
    domain_get_active,
    aem_audit_domain_rules,
]

_ALL_SERVER_TOOLS = _CORE_SERVER_TOOLS + _REGISTRY_AND_DOMAIN_TOOLS
_ACTIVE_SCOPING: dict[str, Any] = {}
_REGISTERED_TOOL_NAMES: list[str] = []


def _parse_early_scoping_args(argv: list[str] | None = None) -> argparse.Namespace:
    target_argv = argv if argv is not None else sys.argv[1:]
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--instance", default="")
    parser.add_argument("--profile", default="")
    parser.add_argument("--env", default="")
    known, _ = parser.parse_known_args(target_argv)
    return known


def _apply_runtime_scoping_and_register_tools(
    instance_id: str = "",
    profile_preset: str = "",
    env_id: str = "",
    allowed_tools_csv: str = "",
) -> dict[str, Any]:
    global _ACTIVE_SCOPING, _REGISTERED_TOOL_NAMES, _IMS_AUTH

    early = _parse_early_scoping_args()
    scoping = CONFIGURATOR_STORE.resolve_runtime_scoping(
        instance_id=instance_id or early.instance,
        profile_preset=profile_preset or early.profile,
        env_id=env_id or early.env,
        allowed_tools_csv=allowed_tools_csv,
    )
    _ACTIVE_SCOPING = scoping

    env_cfg = scoping.get("environment")
    if isinstance(env_cfg, dict):
        if "AEM_MODE" not in os.environ and "AEM_MCP_MODE" not in os.environ and env_cfg.get("mode"):
            CONFIG.mode = env_cfg["mode"]
        if "AEM_URL" not in os.environ and env_cfg.get("aem_url"):
            CONFIG.aem_url = str(env_cfg["aem_url"]).rstrip("/")
        if "AEM_MCP_ENABLE_REGISTRY" not in os.environ and "enable_registry" in env_cfg:
            CONFIG.enable_registry = bool(env_cfg["enable_registry"])
        if "AEM_DOMAIN" not in os.environ and env_cfg.get("default_domain"):
            try:
                DOMAIN_MANAGER.switch_domain(str(env_cfg["default_domain"]))
            except Exception:
                pass
        if CONFIG.mode == "cloud" and _IMS_AUTH is None:
            _IMS_AUTH = AdobeImsAuthProvider()
        _reload_simulator_for_active_domain()

    allowed = scoping.get("allowed_tools")
    _REGISTERED_TOOL_NAMES = []

    if allowed is not None:
        allowed_set = set(allowed)
        if os.getenv("AEM_MCP_ENABLE_REGISTRY", "").lower() in ("false", "0", "no"):
            allowed_set -= {fn.__name__ for fn in _REGISTRY_AND_DOMAIN_TOOLS}
        for fn in _ALL_SERVER_TOOLS:
            if fn.__name__ in allowed_set:
                mcp.tool()(fn)
                _REGISTERED_TOOL_NAMES.append(fn.__name__)
    else:
        for fn in _CORE_SERVER_TOOLS:
            mcp.tool()(fn)
            _REGISTERED_TOOL_NAMES.append(fn.__name__)
        if CONFIG.enable_registry:
            for fn in _REGISTRY_AND_DOMAIN_TOOLS:
                mcp.tool()(fn)
                _REGISTERED_TOOL_NAMES.append(fn.__name__)

    return scoping


_apply_runtime_scoping_and_register_tools()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="aem-mcp",
        description="AEM Content Intelligence MCP Server & Interactive Instance Configurator",
    )
    parser.add_argument(
        "--configurator",
        "--ui",
        action="store_true",
        dest="configurator",
        help="Launch the interactive Web Configurator UI (ZohoMCP-style role & environment toolset manager).",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Host for the Configurator UI server (default: 127.0.0.1).")
    parser.add_argument("--port", type=int, default=8765, help="Port for the Configurator UI server (default: 8765).")
    parser.add_argument("--no-browser", action="store_true", help="Do not automatically open the browser when starting Configurator UI.")
    parser.add_argument("--instance", default="", help="Run a specific configured MCP instance ID (e.g. aem-dev-stg, aem-author-prod).")
    parser.add_argument("--profile", default="", help="Run with a role preset toolset (developer, content-author, data-analyst, pure-aem, full, blank).")
    parser.add_argument("--env", default="", help="Target environment tier (dev, int, qa, stg, prod).")
    parser.add_argument("--export-config", action="store_true", help="Print generated mcpServers client JSON configuration and exit.")

    args, _ = parser.parse_known_args(argv)

    if args.configurator:
        run_configurator_server(host=args.host, port=args.port, open_browser=not args.no_browser)
        return

    if args.export_config:
        targets = [args.instance] if args.instance else None
        print(json.dumps(CONFIGURATOR_STORE.generate_client_config(targets), indent=2))
        return

    mcp.run()


if __name__ == "__main__":
    main()

