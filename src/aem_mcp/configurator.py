"""
MCP Instance, Role Preset & Environment Configurator for AEM Content Intelligence.

Provides a ZohoMCP-style configuration engine and local web UI that enables developers to:
1. Manage AEM environments across lifecycle tiers (dev, int, qa, stg, prod).
2. Create multiple MCP instances from scratch ('blank') or from role presets
   ('developer', 'content-author', 'data-analyst', 'pure-aem', 'full').
3. Independently authorize/scope the exact toolset for each MCP instance
   (e.g., multiple 'developer' instances pointing to INT, STG, and PROD with
   different tool authorizations).
4. Export ready-to-use MCP client configurations for Claude Desktop, Cursor,
   VS Code, and Antigravity.
"""

from __future__ import annotations

import json
import os
import re
import threading
import webbrowser
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from aem_mcp.config import ROOT_DIR

PROFILES_DIR = ROOT_DIR / "profiles"
STATE_FILE = PROFILES_DIR / "configurator_state.json"

_SLUG_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")

# ---------------------------------------------------------------------------
# 1. Canonical Tool Catalog (26 Tools across 6 Functional Categories)
# ---------------------------------------------------------------------------

TOOL_CATEGORIES: list[dict[str, str]] = [
    {
        "id": "jcr_core",
        "name": "JCR Repository & QueryBuilder",
        "description": "Core read-only Sling JSON tree inspection, reference discovery, and QueryBuilder execution.",
    },
    {
        "id": "oak_performance",
        "name": "Oak Index Linter & SQL2 Compiler",
        "description": "QueryBuilder-to-SQL2 compilation, traversal detection, and /oak:index remediation generation.",
    },
    {
        "id": "datasets",
        "name": "Dataset Materialization & Analytics",
        "description": "Out-of-context SQLite dataset streaming, external file ingestion, C-speed analytics, and XLSX/CSV export.",
    },
    {
        "id": "content_audit",
        "name": "Content Integrity & Governance Audits",
        "description": "Automated audits for broken DAM assets, expired promos, incomplete CFs, localization parity, and cross-system drift.",
    },
    {
        "id": "crx_packmgr",
        "name": "CRX Package Manager & Environment Sync",
        "description": "Environment-scoped CRX package creation, filter configuration, build, private exchange handle download/upload, and installation.",
    },
    {
        "id": "sandbox",
        "name": "In-Memory Python Sandbox & Feedback",
        "description": "AST-guarded in-memory Python calculation sandbox and agent tool recommendation logging.",
    },
    {
        "id": "registry_domains",
        "name": "Reference Entity Registry & Multi-Domain",
        "description": "Starter-kit reference master data facade (Hospitality SQLite, Automotive CSV, Retail JSON) and domain switching.",
    },
]


TOOL_CATALOG: list[dict[str, Any]] = [
    # JCR Core
    {
        "name": "aem_json",
        "category": "jcr_core",
        "description": "Read an AEM repository path as JSON using Sling selector conventions (depth 0..3).",
        "tokens": 95,
        "badge": "read-only",
    },
    {
        "name": "aem_traverse",
        "category": "jcr_core",
        "description": "Explore an AEM node and its immediate descendant levels (bounded up to depth 3).",
        "tokens": 90,
        "badge": "read-only",
    },
    {
        "name": "aem_querybuilder",
        "category": "jcr_core",
        "description": "Run a bounded, read-only AEM QueryBuilder query with execution timing telemetry.",
        "tokens": 115,
        "badge": "read-only",
    },
    {
        "name": "aem_find_references",
        "category": "jcr_core",
        "description": "Find repository paths (templates, models, fragments, assets) referenced by an AEM node.",
        "tokens": 95,
        "badge": "read-only",
    },
    {
        "name": "aem_discover_properties",
        "category": "jcr_core",
        "description": "Discover observed JCR properties and sample values across representative child nodes.",
        "tokens": 120,
        "badge": "read-only",
    },
    # Oak Performance
    {
        "name": "aem_compile_querybuilder_sql2",
        "category": "oak_performance",
        "description": "Compile an AEM QueryBuilder predicate map into an optimized Jackrabbit Oak JCR-SQL2 query.",
        "tokens": 110,
        "badge": "compiler",
    },
    {
        "name": "aem_lint_query_indexing",
        "category": "oak_performance",
        "description": "Lint QueryBuilder queries for unindexed Oak traversals and generate JSON, FileVault XML, and Repoinit indexes.",
        "tokens": 145,
        "badge": "linter",
    },
    # Datasets
    {
        "name": "aem_query_dataset",
        "category": "datasets",
        "description": "Stream and materialize paginated QueryBuilder results into an on-disk SQLite dataset.",
        "tokens": 140,
        "badge": "local-sqlite",
    },
    {
        "name": "dataset_import",
        "category": "datasets",
        "description": "Import external structured files (CSV, JSON, JSONL) from other MCPs into an on-disk SQLite dataset.",
        "tokens": 130,
        "badge": "file-ingest",
    },
    {
        "name": "dataset_info",
        "category": "datasets",
        "description": "Retrieve metadata and schema information for a materialized dataset.",
        "tokens": 80,
        "badge": "local-sqlite",
    },
    {
        "name": "dataset_get_rows",
        "category": "datasets",
        "description": "Read a bounded window of rows from a materialized dataset.",
        "tokens": 95,
        "badge": "local-sqlite",
    },
    {
        "name": "dataset_analyze",
        "category": "datasets",
        "description": "Run server-side SQLite JSON1 aggregations (count, missing, group_by, stats, filter).",
        "tokens": 180,
        "badge": "local-sqlite",
    },
    {
        "name": "dataset_match",
        "category": "datasets",
        "description": "Stream-join two datasets on a key to identify matches, orphans, and field-level discrepancies.",
        "tokens": 140,
        "badge": "local-sqlite",
    },
    {
        "name": "dataset_export",
        "category": "datasets",
        "description": "Generate a downloadable CSV or Excel (.xlsx) export artifact via local HTTP server.",
        "tokens": 100,
        "badge": "export",
    },
    {
        "name": "dataset_discard",
        "category": "datasets",
        "description": "Delete a temporary materialized dataset and free disk space.",
        "tokens": 80,
        "badge": "local-sqlite",
    },
    # Content Audits
    {
        "name": "aem_audit_content_integrity",
        "category": "content_audit",
        "description": "Audit AEM pages for broken DAM assets, expired promo fragments, and incomplete Content Fragments.",
        "tokens": 145,
        "badge": "audit",
    },
    {
        "name": "aem_audit_localization_coverage",
        "category": "content_audit",
        "description": "Audit multi-region localization coverage comparing base locale against regional target sites.",
        "tokens": 135,
        "badge": "audit",
    },
    {
        "name": "aem_audit_cross_reference",
        "category": "content_audit",
        "description": "Cross-reference AEM authored properties against Canonical Master Entity Registry.",
        "tokens": 125,
        "badge": "audit",
    },
    {
        "name": "aem_audit_domain_rules",
        "category": "content_audit",
        "description": "Execute declarative AST governance rules from domain.yaml against JCR and Master Registry.",
        "tokens": 125,
        "badge": "audit",
    },
    # CRX Package Manager & Environment Sync
    {
        "name": "package_list",
        "category": "crx_packmgr",
        "description": "Read-only: List CRX packages in the active MCP instance's AEM environment.",
        "tokens": 90,
        "badge": "read-only",
    },
    {
        "name": "package_create",
        "category": "crx_packmgr",
        "description": "Create an empty CRX package definition in the active environment with optional filter roots (requires confirm=True).",
        "tokens": 135,
        "badge": "confirm-gate",
    },
    {
        "name": "package_set_filters",
        "category": "crx_packmgr",
        "description": "Replace repository filter roots on an existing package definition via update.jsp (requires confirm=True).",
        "tokens": 130,
        "badge": "confirm-gate",
    },
    {
        "name": "package_build",
        "category": "crx_packmgr",
        "description": "Assemble/build a configured CRX package in the active environment (requires confirm=True).",
        "tokens": 115,
        "badge": "confirm-gate",
    },
    {
        "name": "package_download",
        "category": "crx_packmgr",
        "description": "Read-only: Stage built package bytes into private MCP exchange storage and return an opaque package_handle (0 context bytes).",
        "tokens": 120,
        "badge": "read-only",
    },
    {
        "name": "package_upload",
        "category": "crx_packmgr",
        "description": "Upload a staged package_handle from private exchange storage into the active environment (requires confirm=True).",
        "tokens": 125,
        "badge": "confirm-gate",
    },
    {
        "name": "package_install",
        "category": "crx_packmgr",
        "description": "Install an uploaded or built CRX package into the active environment's repository (requires confirm=True).",
        "tokens": 120,
        "badge": "confirm-gate",
    },
    # Sandbox & Feedback
    {
        "name": "execute_code",
        "category": "sandbox",
        "description": "Run bounded, AST-sandboxed in-memory Python calculations with strict timeout enforcement.",
        "tokens": 115,
        "badge": "sandbox-exec",
    },
    {
        "name": "submit_tool_recommendation",
        "category": "sandbox",
        "description": "Submit a structured suggestion for a new or improved MCP capability.",
        "tokens": 115,
        "badge": "feedback",
    },
    # Reference Registry & Multi-Domain
    {
        "name": "registry_indexes",
        "category": "registry_domains",
        "description": "List available Master Entity Registry tables and record counts.",
        "tokens": 80,
        "badge": "reference-facade",
    },
    {
        "name": "registry_search",
        "category": "registry_domains",
        "description": "Search Master Entity Registry records with structured filters.",
        "tokens": 120,
        "badge": "reference-facade",
    },
    {
        "name": "registry_get",
        "category": "registry_domains",
        "description": "Retrieve one canonical business entity record by primary identifier.",
        "tokens": 95,
        "badge": "reference-facade",
    },
    {
        "name": "registry_query_dataset",
        "category": "registry_domains",
        "description": "Materialize records from the active Master Entity Registry into an SQLite dataset table.",
        "tokens": 135,
        "badge": "reference-facade",
    },
    {
        "name": "domain_list",
        "category": "registry_domains",
        "description": "List available enterprise domain profiles (Hospitality, Automotive, Retail).",
        "tokens": 85,
        "badge": "reference-facade",
    },
    {
        "name": "domain_switch",
        "category": "registry_domains",
        "description": "Switch the active enterprise domain profile and reload simulator/registry state.",
        "tokens": 95,
        "badge": "reference-facade",
    },
    {
        "name": "domain_get_active",
        "category": "registry_domains",
        "description": "Get detailed schema, field mapping, and rule metadata for the active domain profile.",
        "tokens": 90,
        "badge": "reference-facade",
    },
]

ALL_TOOL_NAMES: list[str] = [t["name"] for t in TOOL_CATALOG]
_PROD_MUTATING_TOOLS: set[str] = {
    "execute_code",
    "package_create",
    "package_set_filters",
    "package_build",
    "package_upload",
    "package_install",
}

# ---------------------------------------------------------------------------
# 2. Initial Environment Set (dev, int, qa, stg, prod)
# ---------------------------------------------------------------------------

DEFAULT_ENVIRONMENTS: dict[str, dict[str, Any]] = {
    "dev": {
        "id": "dev",
        "name": "DEV (Local Simulator / Author)",
        "tier": "dev",
        "mode": "mock",
        "aem_url": "http://localhost:4502",
        "auth_type": "basic",
        "default_domain": "hospitality",
        "enable_registry": True,
        "description": "Local offline JCR simulator and developer sandbox.",
    },
    "int": {
        "id": "int",
        "name": "INT (Integration Environment)",
        "tier": "int",
        "mode": "onprem",
        "aem_url": "https://aem-int.internal.example.com",
        "auth_type": "basic",
        "default_domain": "hospitality",
        "enable_registry": True,
        "description": "Shared continuous integration AEM Author instance for branch testing.",
    },
    "qa": {
        "id": "qa",
        "name": "QA (Quality Assurance)",
        "tier": "qa",
        "mode": "cloud",
        "aem_url": "https://author-p10001-e20001.adobeaemcloud.com",
        "auth_type": "ims",
        "default_domain": "hospitality",
        "enable_registry": True,
        "description": "Dedicated QA environment for automated content integrity and localization audits.",
    },
    "stg": {
        "id": "stg",
        "name": "STG (Staging / Pre-Prod)",
        "tier": "stg",
        "mode": "cloud",
        "aem_url": "https://author-p10001-e20002.adobeaemcloud.com",
        "auth_type": "ims",
        "default_domain": "hospitality",
        "enable_registry": False,
        "description": "Pre-production AEM as a Cloud Service staging author environment.",
    },
    "prod": {
        "id": "prod",
        "name": "PROD (Production Author)",
        "tier": "prod",
        "mode": "cloud",
        "aem_url": "https://author-p10001-e20003.adobeaemcloud.com",
        "auth_type": "ims",
        "default_domain": "hospitality",
        "enable_registry": False,
        "description": "Live production AEM Author environment (strict least-privilege tool scoping recommended).",
    },
}

# ---------------------------------------------------------------------------
# 3. Role Presets (Including 'blank' from-scratch & persona templates)
# ---------------------------------------------------------------------------

ROLE_PRESETS: dict[str, dict[str, Any]] = {
    "blank": {
        "id": "blank",
        "name": "Custom (Start from Scratch)",
        "description": "Zero tools pre-selected. Hand-pick the exact capabilities required for a custom agent.",
        "tools": [],
    },
    "developer": {
        "id": "developer",
        "name": "AEM Developer / Oak Architect",
        "description": "JCR tree inspection, QueryBuilder, JCR-SQL2 compiler, Oak index linter, CRX Package Manager sync, datasets, and Python sandbox.",
        "tools": [
            "aem_json",
            "aem_traverse",
            "aem_querybuilder",
            "aem_find_references",
            "aem_discover_properties",
            "aem_compile_querybuilder_sql2",
            "aem_lint_query_indexing",
            "aem_query_dataset",
            "dataset_import",
            "dataset_info",
            "dataset_get_rows",
            "dataset_analyze",
            "dataset_match",
            "dataset_export",
            "dataset_discard",
            "package_list",
            "package_create",
            "package_set_filters",
            "package_build",
            "package_download",
            "package_upload",
            "package_install",
            "execute_code",
            "submit_tool_recommendation",
        ],
    },
    "developer-readonly": {
        "id": "developer-readonly",
        "name": "AEM Developer (PROD Read-Only)",
        "description": "Read-only developer & Oak architect toolset (JCR, QueryBuilder, SQL2 compiler, Oak index linter, datasets, package_list/download) with all mutating tools stripped.",
        "tools": [
            "aem_json",
            "aem_traverse",
            "aem_querybuilder",
            "aem_find_references",
            "aem_discover_properties",
            "aem_compile_querybuilder_sql2",
            "aem_lint_query_indexing",
            "aem_query_dataset",
            "dataset_import",
            "dataset_info",
            "dataset_get_rows",
            "dataset_analyze",
            "dataset_match",
            "dataset_export",
            "dataset_discard",
            "package_list",
            "package_download",
            "submit_tool_recommendation",
        ],
    },
    "content-author": {
        "id": "content-author",
        "name": "Content Author & Localization QA",
        "description": "Content integrity audits, broken DAM asset detection, localization coverage, and Excel/CSV reporting (no code sandbox or Oak index internals).",
        "tools": [
            "aem_json",
            "aem_traverse",
            "aem_querybuilder",
            "aem_find_references",
            "aem_audit_content_integrity",
            "aem_audit_localization_coverage",
            "aem_audit_cross_reference",
            "aem_audit_domain_rules",
            "dataset_export",
        ],
    },
    "data-analyst": {
        "id": "data-analyst",
        "name": "Cross-System Data & Reconciliation Analyst",
        "description": "Bulk JCR dataset extraction, external CSV/JSON ingestion (dataset_import), cross-system matching, and statistical aggregations.",
        "tools": [
            "aem_querybuilder",
            "aem_discover_properties",
            "aem_query_dataset",
            "dataset_import",
            "dataset_info",
            "dataset_get_rows",
            "dataset_analyze",
            "dataset_match",
            "dataset_export",
            "dataset_discard",
            "aem_audit_cross_reference",
            "aem_audit_domain_rules",
        ],
    },
    "pure-aem": {
        "id": "pure-aem",
        "name": "Pure AEM Core (No Demo Registry)",
        "description": "All 28 core AEM JCR, Oak performance, CRX Package Manager, content audit, and dataset tools without starter-kit reference registry tools.",
        "tools": [
            t["name"] for t in TOOL_CATALOG if t["category"] != "registry_domains"
        ],
    },
    "full": {
        "id": "full",
        "name": "Full Starter Kit (All 35 Tools)",
        "description": "Unrestricted access to all JCR, Oak indexing, CRX Package Manager, Dataset, Audit, Sandbox, and Multi-Domain Reference Registry tools.",
        "tools": list(ALL_TOOL_NAMES),
    },
}

# Precomputed O(1) bitmask lookup table for fast preset matching
_TOOL_BIT_INDEX: dict[str, int] = {name: (1 << idx) for idx, name in enumerate(ALL_TOOL_NAMES)}


def compute_toolset_mask(tools: list[str] | set[str]) -> int:
    """Encode a tool list/set into a single integer bitmask in O(k) bitwise OR passes."""
    mask = 0
    for name in tools:
        mask |= _TOOL_BIT_INDEX.get(name, 0)
    return mask


_PRESET_MASK_TO_ID: dict[int, str] = {
    compute_toolset_mask(preset["tools"]): preset_id
    for preset_id, preset in ROLE_PRESETS.items()
}


def detect_matching_preset(tools: list[str] | set[str]) -> str:
    """
    Fast O(1) bitmask hash-map lookup that matches a toolset to its exact role preset ID,
    or returns 'custom' when the toolset has been customized away from all standard presets.
    """
    return _PRESET_MASK_TO_ID.get(compute_toolset_mask(tools), "custom")


def _default_instances() -> dict[str, dict[str, Any]]:
    """Pre-seeded multi-environment instances demonstrating role x environment scoping."""
    dev_tools = list(ROLE_PRESETS["developer"]["tools"])
    dev_prod_tools = list(ROLE_PRESETS["developer-readonly"]["tools"])
    author_tools = list(ROLE_PRESETS["content-author"]["tools"])

    return {
        "aem-dev-local": {
            "id": "aem-dev-local",
            "name": "AEM Developer (DEV Simulator)",
            "role_preset": "developer",
            "environment": "dev",
            "enabled": True,
            "root_path_override": "",
            "allowed_tools": dev_tools,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
        "aem-dev-int": {
            "id": "aem-dev-int",
            "name": "AEM Developer (INT)",
            "role_preset": "developer",
            "environment": "int",
            "enabled": True,
            "root_path_override": "",
            "allowed_tools": dev_tools,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
        "aem-dev-stg": {
            "id": "aem-dev-stg",
            "name": "AEM Developer (STG)",
            "role_preset": "developer",
            "environment": "stg",
            "enabled": True,
            "root_path_override": "",
            "allowed_tools": dev_tools,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
        "aem-dev-prod": {
            "id": "aem-dev-prod",
            "name": "AEM Developer (PROD Read-Only)",
            "role_preset": "developer-readonly",
            "environment": "prod",
            "enabled": True,
            "root_path_override": "",
            "allowed_tools": dev_prod_tools,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
        "aem-author-qa": {
            "id": "aem-author-qa",
            "name": "Content Author & QA (QA)",
            "role_preset": "content-author",
            "environment": "qa",
            "enabled": True,
            "root_path_override": "/content/novaria",
            "allowed_tools": author_tools,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
        "aem-author-prod": {
            "id": "aem-author-prod",
            "name": "Content Author & QA (PROD)",
            "role_preset": "content-author",
            "environment": "prod",
            "enabled": True,
            "root_path_override": "/content/novaria",
            "allowed_tools": author_tools,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
    }


DEFAULT_INSTANCE_TEMPLATES: dict[str, dict[str, Any]] = _default_instances()


class ConfiguratorStore:
    """Manages persistence of environments and scoped MCP instances in profiles/."""

    def __init__(self, profiles_dir: Path | None = None):
        self.profiles_dir = profiles_dir or PROFILES_DIR
        self.state_file = self.profiles_dir / "configurator_state.json"
        self.environments: dict[str, dict[str, Any]] = {}
        self.instances: dict[str, dict[str, Any]] = {}
        self.load()

    def load(self) -> None:
        if self.state_file.exists():
            try:
                data = json.loads(self.state_file.read_text(encoding="utf-8"))
                self.environments = (
                    data["environments"]
                    if isinstance(data.get("environments"), dict) and len(data["environments"]) > 0
                    else json.loads(json.dumps(DEFAULT_ENVIRONMENTS))
                )
                self.instances = (
                    data["instances"]
                    if isinstance(data.get("instances"), dict)
                    else _default_instances()
                )
                for inst in self.instances.values():
                    if isinstance(inst, dict) and "enabled" not in inst:
                        inst["enabled"] = True
                return
            except Exception:
                pass
        self.environments = json.loads(json.dumps(DEFAULT_ENVIRONMENTS))
        self.instances = _default_instances()

    def save(self) -> None:
        self.profiles_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "environments": self.environments,
            "instances": self.instances,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self.state_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        # Also write individual instance JSON files for direct CLI inspection
        for inst_id, inst in self.instances.items():
            if _SLUG_RE.fullmatch(inst_id):
                env_cfg = self.environments.get(inst.get("environment", "dev"), DEFAULT_ENVIRONMENTS["dev"])
                resolved = {**inst, "resolved_environment": env_cfg}
                (self.profiles_dir / f"{inst_id}.json").write_text(
                    json.dumps(resolved, indent=2), encoding="utf-8"
                )

    def upsert_environment(self, env_data: dict[str, Any]) -> dict[str, Any]:
        env_id = str(env_data.get("id", "")).strip().lower()
        if not _SLUG_RE.fullmatch(env_id):
            raise ValueError(f"Invalid environment ID '{env_id}'. Use alphanumeric, '-', or '_'.")
        mode = str(env_data.get("mode", "mock")).strip().lower()
        if mode not in ("mock", "onprem", "cloud"):
            raise ValueError(f"Invalid mode '{mode}'. Use 'mock', 'onprem', or 'cloud'.")

        entry = {
            "id": env_id,
            "name": str(env_data.get("name") or env_id.upper()).strip(),
            "tier": str(env_data.get("tier") or env_id).strip().lower(),
            "mode": mode,
            "aem_url": str(env_data.get("aem_url") or "http://localhost:4502").strip().rstrip("/"),
            "auth_type": str(env_data.get("auth_type") or "basic").strip().lower(),
            "default_domain": str(env_data.get("default_domain") or "hospitality").strip().lower(),
            "enable_registry": bool(env_data.get("enable_registry", mode == "mock")),
            "description": str(env_data.get("description") or "").strip(),
        }
        self.environments[env_id] = entry
        self.save()
        return entry

    def delete_environment(self, env_id: str) -> bool:
        clean_id = env_id.strip().lower()
        if clean_id not in self.environments:
            return False
        self.environments.pop(clean_id)
        self.save()
        return True

    def upsert_instance(self, inst_data: dict[str, Any]) -> dict[str, Any]:
        inst_id = str(inst_data.get("id", "")).strip().lower()
        if not _SLUG_RE.fullmatch(inst_id):
            raise ValueError(f"Invalid instance ID '{inst_id}'. Use alphanumeric, '-', or '_'.")

        env_id = str(inst_data.get("environment") or "dev").strip().lower()
        if env_id not in self.environments:
            raise ValueError(f"Unknown environment '{env_id}'. Available: {', '.join(self.environments.keys())}")

        role_preset = str(inst_data.get("role_preset") or "blank").strip().lower()
        raw_tools = inst_data.get("allowed_tools")
        if raw_tools is None:
            preset_obj = ROLE_PRESETS.get(role_preset, ROLE_PRESETS["blank"])
            allowed_tools = list(preset_obj["tools"])
            role_preset = detect_matching_preset(allowed_tools)
        else:
            valid_set = set(ALL_TOOL_NAMES)
            allowed_tools = [t for t in raw_tools if t in valid_set]
            role_preset = detect_matching_preset(allowed_tools)

        existing = self.instances.get(inst_id, {})
        raw_enabled = inst_data.get("enabled")
        enabled = bool(raw_enabled) if raw_enabled is not None else bool(existing.get("enabled", True))

        entry = {
            "id": inst_id,
            "name": str(inst_data.get("name") or inst_id).strip(),
            "role_preset": role_preset,
            "environment": env_id,
            "enabled": enabled,
            "root_path_override": str(inst_data.get("root_path_override") or "").strip(),
            "allowed_tools": allowed_tools,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self.instances[inst_id] = entry
        self.save()
        return entry

    def set_instance_enabled(self, inst_id: str, enabled: bool) -> dict[str, Any]:
        """Enable or disable a configured MCP instance without deleting its saved configuration."""
        clean_id = inst_id.strip().lower()
        if clean_id not in self.instances:
            raise ValueError(f"Unknown instance '{inst_id}'.")
        self.instances[clean_id]["enabled"] = bool(enabled)
        self.instances[clean_id]["updated_at"] = datetime.now(timezone.utc).isoformat()
        self.save()
        return self.instances[clean_id]

    def clone_role_across_environments(
        self,
        base_id_prefix: str,
        name_prefix: str,
        role_preset: str,
        environment_ids: list[str],
        allowed_tools: list[str] | None = None,
        disable_sandbox_in_prod: bool = True,
    ) -> list[dict[str, Any]]:
        """
        Batch-create or update multiple MCP instances for a role across multiple environment levels
        (e.g., aem-dev-dev, aem-dev-int, aem-dev-qa, aem-dev-stg, aem-dev-prod).
        """
        preset = ROLE_PRESETS.get(role_preset, ROLE_PRESETS["developer"])
        base_tools = list(allowed_tools) if allowed_tools is not None else list(preset["tools"])
        created = []
        for env_id in environment_ids:
            clean_env = env_id.strip().lower()
            if clean_env not in self.environments:
                continue
            tools_for_env = list(base_tools)
            if disable_sandbox_in_prod and clean_env == "prod":
                tools_for_env = [t for t in tools_for_env if t not in _PROD_MUTATING_TOOLS]
            inst_id = f"{base_id_prefix.strip().lower()}-{clean_env}"
            inst_name = f"{name_prefix.strip()} ({clean_env.upper()})"
            created.append(
                self.upsert_instance({
                    "id": inst_id,
                    "name": inst_name,
                    "role_preset": role_preset,
                    "environment": clean_env,
                    "allowed_tools": tools_for_env,
                })
            )
        return created

    def delete_instance(self, inst_id: str) -> bool:
        clean_id = inst_id.strip().lower()
        if clean_id not in self.instances:
            return False
        self.instances.pop(clean_id)
        inst_file = self.profiles_dir / f"{clean_id}.json"
        if inst_file.exists():
            inst_file.unlink(missing_ok=True)
        self.save()
        return True

    def reorder_instances(self, ordered_ids: list[str]) -> list[str]:
        """
        Reorder configured MCP instances according to ordered_ids while preserving any
        instances not explicitly listed at the end.
        """
        reordered: dict[str, dict[str, Any]] = {}
        for raw_id in ordered_ids:
            clean_id = str(raw_id).strip().lower()
            if clean_id in self.instances and clean_id not in reordered:
                reordered[clean_id] = self.instances[clean_id]
        for k, v in self.instances.items():
            if k not in reordered:
                reordered[k] = v
        self.instances = reordered
        self.save()
        return list(self.instances.keys())

    def recreate_from_default_template(
        self,
        template_id: str,
        new_id: str = "",
        new_name: str = "",
        env_id: str = "",
    ) -> dict[str, Any]:
        """
        Recreate a deleted (or cloned) default instance from its canonical built-in template
        ('aem-dev-local', 'aem-dev-int', 'aem-dev-stg', 'aem-dev-prod', 'aem-author-qa', 'aem-author-prod').
        Automatically restores the target default environment if it was previously deleted.
        """
        defaults = _default_instances()
        clean_template_id = template_id.strip().lower()
        if clean_template_id not in defaults:
            raise ValueError(
                f"Unknown default instance template '{template_id}'. "
                f"Available templates: {', '.join(defaults.keys())}"
            )
        tpl = defaults[clean_template_id]
        target_env = (env_id or tpl["environment"]).strip().lower()
        if target_env not in self.environments and target_env in DEFAULT_ENVIRONMENTS:
            self.environments[target_env] = json.loads(json.dumps(DEFAULT_ENVIRONMENTS[target_env]))

        return self.upsert_instance({
            "id": (new_id or tpl["id"]).strip().lower(),
            "name": (new_name or tpl["name"]).strip(),
            "role_preset": tpl["role_preset"],
            "environment": target_env,
            "enabled": True,
            "root_path_override": tpl.get("root_path_override", ""),
            "allowed_tools": list(tpl["allowed_tools"]),
        })

    def restore_default_instances(
        self,
        instance_ids: list[str] | None = None,
        overwrite: bool = False,
    ) -> list[dict[str, Any]]:
        """
        Restore missing (or all, when overwrite=True) built-in default instances and their default environments.
        """
        defaults = _default_instances()
        targets = [i.strip().lower() for i in instance_ids] if instance_ids else list(defaults.keys())
        restored: list[dict[str, Any]] = []

        for tid in targets:
            if tid not in defaults:
                continue
            if not overwrite and tid in self.instances:
                continue
            restored.append(self.recreate_from_default_template(tid))

        return restored

    def resolve_runtime_scoping(
        self,
        instance_id: str = "",
        profile_preset: str = "",
        env_id: str = "",
        allowed_tools_csv: str = "",
    ) -> dict[str, Any]:
        """
        Resolves the active environment and authorized toolset when the MCP server starts.
        Priority:
          1. Explicit instance_id (from --instance or AEM_MCP_INSTANCE)
          2. Explicit profile_preset / env_id / allowed_tools_csv
          3. Unscoped default (all tools enabled based on CONFIG.enable_registry)
        """
        inst_id = (instance_id or os.getenv("AEM_MCP_INSTANCE", "")).strip().lower()
        preset_id = (profile_preset or os.getenv("AEM_MCP_PROFILE", "")).strip().lower()
        target_env_id = (env_id or os.getenv("AEM_MCP_ENV", "")).strip().lower()
        tools_csv = (allowed_tools_csv or os.getenv("AEM_MCP_ALLOWED_TOOLS", "")).strip()

        resolved_env = None
        allowed_tools: set[str] | None = None
        active_label = "default-unscoped"
        is_enabled = True

        if inst_id and inst_id in self.instances:
            inst = self.instances[inst_id]
            active_label = inst["id"]
            preset_id = inst.get("role_preset", "custom")
            env_key = target_env_id or inst.get("environment", "dev")
            resolved_env = self.environments.get(env_key)
            is_enabled = bool(inst.get("enabled", True))
            allowed_tools = set(inst.get("allowed_tools", ALL_TOOL_NAMES)) if is_enabled else set()
        else:
            if target_env_id and target_env_id in self.environments:
                resolved_env = self.environments[target_env_id]
            if preset_id and preset_id in ROLE_PRESETS:
                allowed_tools = set(ROLE_PRESETS[preset_id]["tools"])
                active_label = f"preset:{preset_id}"

        if tools_csv and is_enabled:
            custom_list = [t.strip() for t in tools_csv.split(",") if t.strip()]
            allowed_tools = set(custom_list)

        return {
            "active_instance": inst_id or active_label,
            "role_preset": preset_id or "full",
            "environment": resolved_env,
            "enabled": is_enabled,
            "allowed_tools": allowed_tools,
        }

    def generate_client_config(
        self,
        instance_ids: list[str] | None = None,
        include_disabled: bool = False,
    ) -> dict[str, Any]:
        """
        Generates ready-to-paste MCP client configuration JSON (`mcpServers`)
        for one or more configured instances. By default, disabled instances are
        excluded from bulk exports unless explicitly requested in `instance_ids`
        or `include_disabled=True`.
        """
        explicit_ids = instance_ids is not None
        targets = instance_ids if explicit_ids else list(self.instances.keys())
        mcp_servers: dict[str, Any] = {}

        for inst_id in targets:
            inst = self.instances.get(inst_id)
            if not inst:
                continue
            inst_enabled = bool(inst.get("enabled", True))
            if not inst_enabled and not include_disabled and not explicit_ids:
                continue
            env_id = inst.get("environment", "dev")
            env_cfg = self.environments.get(env_id, DEFAULT_ENVIRONMENTS["dev"])
            env_vars: dict[str, str] = {
                "AEM_MCP_INSTANCE": inst["id"],
                "AEM_MCP_ENV": env_id,
                "AEM_MODE": env_cfg["mode"],
                "AEM_URL": env_cfg["aem_url"],
                "AEM_DOMAIN": env_cfg.get("default_domain", "hospitality"),
                "AEM_MCP_ENABLE_REGISTRY": "true" if env_cfg.get("enable_registry", True) else "false",
                "AEM_MCP_ALLOWED_TOOLS": ",".join(inst.get("allowed_tools", [])),
            }
            if env_cfg["mode"] == "onprem":
                env_vars["AEM_USERNAME"] = "admin"
                env_vars["AEM_PASSWORD"] = "<YOUR_AEM_PASSWORD>"
            elif env_cfg["mode"] == "cloud":
                env_vars["AEM_IMS_CLIENT_ID"] = "<YOUR_IMS_CLIENT_ID>"
                env_vars["AEM_IMS_CLIENT_SECRET"] = "<YOUR_IMS_CLIENT_SECRET>"
                env_vars["AEM_IMS_ACCESS_TOKEN"] = "<OPTIONAL_PRE_GENERATED_TOKEN>"

            server_entry: dict[str, Any] = {
                "command": "aem-mcp",
                "args": ["--instance", inst["id"]],
                "env": env_vars,
            }
            if not inst_enabled:
                server_entry["disabled"] = True
            mcp_servers[inst["id"]] = server_entry

        return {"mcpServers": mcp_servers}

    def get_mutable_state(self) -> dict[str, Any]:
        return {
            "environments": self.environments,
            "instances": self.instances,
        }

    def get_full_state(self) -> dict[str, Any]:
        return {
            "categories": TOOL_CATEGORIES,
            "tools": TOOL_CATALOG,
            "presets": ROLE_PRESETS,
            "default_instance_templates": _default_instances(),
            "environments": self.environments,
            "instances": self.instances,
            "client_config_preview": self.generate_client_config(),
        }


CONFIGURATOR_STORE = ConfiguratorStore()


# ---------------------------------------------------------------------------
# 4. Local Web Configurator HTTP Server
# ---------------------------------------------------------------------------

def _load_ui_html() -> str:
    ui_path = Path(__file__).parent / "configurator_ui.html"
    if ui_path.exists():
        raw = ui_path.read_text(encoding="utf-8")
        compact = re.sub(r"<!--.*?-->", "", raw, flags=re.DOTALL)
        compact = re.sub(r"/\*.*?\*/", "", compact, flags=re.DOTALL)

        def _minify_style(m: re.Match[str]) -> str:
            css = re.sub(r"\s+", " ", m.group(1)).strip()
            css = re.sub(r"\s*([{:;,}])\s*", r"\1", css)
            return f"<style>{css}</style>"

        compact = re.sub(r"<style>(.*?)</style>", _minify_style, compact, flags=re.DOTALL)
        lines = [
            line.strip()
            for line in compact.splitlines()
            if line.strip() and not line.strip().startswith("//")
        ]
        return "\n".join(lines)
    return "<html><body><h1>Configurator UI file missing</h1></body></html>"


def create_configurator_handler(store: ConfiguratorStore | None = None):
    active_store = store or CONFIGURATOR_STORE

    class ConfiguratorRequestHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            path = self.path.split("?", 1)[0]
            if path in ("/", "/index.html"):
                html_bytes = _load_ui_html().encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(html_bytes)))
                self.send_header("Connection", "close")
                self.end_headers()
                self._write_all(html_bytes)
                return

            if path == "/api/state":
                self._send_json(200, active_store.get_full_state())
                return

            self._send_json(404, {"error": "Not found"})

        def do_POST(self) -> None:
            path = self.path.split("?", 1)[0]
            try:
                length = int(self.headers.get("Content-Length", 0))
                if length < 0 or length > 1_048_576:
                    self._send_json(413, {"error": "Payload too large"})
                    return
                body = json.loads(self.rfile.read(length).decode("utf-8")) if length > 0 else {}
            except Exception as exc:
                self._send_json(400, {"error": f"Invalid JSON payload: {exc}"})
                return

            try:
                if path == "/api/environments":
                    env = active_store.upsert_environment(body)
                    self._send_json(200, {"success": True, "environment": env, "state": active_store.get_mutable_state()})
                    return

                if path == "/api/environments/delete":
                    deleted = active_store.delete_environment(str(body.get("id", "")))
                    self._send_json(200, {"success": deleted, "state": active_store.get_mutable_state()})
                    return

                if path == "/api/instances":
                    inst = active_store.upsert_instance(body)
                    self._send_json(200, {"success": True, "instance": inst, "state": active_store.get_mutable_state()})
                    return

                if path == "/api/instances/toggle":
                    inst_id = str(body.get("id", "")).strip()
                    enabled = bool(body.get("enabled", True))
                    inst = active_store.set_instance_enabled(inst_id, enabled)
                    self._send_json(200, {"success": True, "instance": inst, "state": active_store.get_mutable_state()})
                    return

                if path == "/api/instances/batch-clone":
                    created = active_store.clone_role_across_environments(
                        base_id_prefix=str(body.get("base_id_prefix", "aem-dev")),
                        name_prefix=str(body.get("name_prefix", "AEM Developer")),
                        role_preset=str(body.get("role_preset", "developer")),
                        environment_ids=list(body.get("environments", ["dev", "int", "qa", "stg", "prod"])),
                        allowed_tools=body.get("allowed_tools"),
                        disable_sandbox_in_prod=bool(body.get("disable_sandbox_in_prod", True)),
                    )
                    self._send_json(200, {"success": True, "created": created, "state": active_store.get_mutable_state()})
                    return

                if path == "/api/instances/restore-defaults":
                    ids_arg = body.get("instance_ids")
                    template_id = str(body.get("template_id", "")).strip()
                    if template_id:
                        recreated = active_store.recreate_from_default_template(
                            template_id=template_id,
                            new_id=str(body.get("new_id") or body.get("id") or ""),
                            new_name=str(body.get("new_name") or body.get("name") or ""),
                            env_id=str(body.get("environment") or ""),
                        )
                        self._send_json(
                            200,
                            {
                                "success": True,
                                "instance": recreated,
                                "restored": [recreated],
                                "state": active_store.get_mutable_state(),
                            },
                        )
                        return
                    restored = active_store.restore_default_instances(
                        instance_ids=list(ids_arg) if isinstance(ids_arg, list) else None,
                        overwrite=bool(body.get("overwrite", False)),
                    )
                    self._send_json(200, {"success": True, "restored": restored, "state": active_store.get_mutable_state()})
                    return

                if path == "/api/instances/delete":
                    deleted = active_store.delete_instance(str(body.get("id", "")))
                    self._send_json(200, {"success": deleted, "state": active_store.get_mutable_state()})
                    return

                if path == "/api/instances/reorder":
                    ordered = body.get("ordered_ids")
                    if not isinstance(ordered, list):
                        raise ValueError("'ordered_ids' must be a list of instance IDs.")
                    new_order = active_store.reorder_instances([str(x) for x in ordered])
                    self._send_json(
                        200,
                        {"success": True, "ordered_ids": new_order, "state": active_store.get_mutable_state()},
                    )
                    return

                self._send_json(404, {"error": f"Unknown endpoint: {path}"})
            except Exception as exc:
                self._send_json(400, {"success": False, "error": str(exc)})

        def _write_all(self, data: bytes) -> None:
            view = memoryview(data)
            total = len(data)
            sent = 0
            while sent < total:
                n = self.wfile.write(view[sent:])
                if not n:
                    break
                sent += n
            self.wfile.flush()
            try:
                self.connection.settimeout(0.25)
                while self.connection.recv(256):
                    pass
            except Exception:
                pass

        def _send_json(self, status: int, data: dict[str, Any]) -> None:
            raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            self._write_all(raw)

        def log_message(self, _format: str, *_args: Any) -> None:
            return

    return ConfiguratorRequestHandler


def run_configurator_server(host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> None:
    """Launch the interactive ZohoMCP-style Web Configurator UI."""
    handler = create_configurator_handler(CONFIGURATOR_STORE)
    server = ThreadingHTTPServer((host, port), handler)
    actual_port = server.server_address[1]
    url = f"http://{host}:{actual_port}"
    print(f"AEM MCP Instance & Toolset Configurator running at {url}")
    print("Press Ctrl+C to stop.")
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
