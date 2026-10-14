"""
Domain Manager and Universal Registry Adapter for AEM Content Intelligence MCP.
Supports SQLite, CSV, and JSON master data sources with declarative governance rules.
"""

from __future__ import annotations

import ast
import csv
import json
import logging
import os
import re
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from aem_mcp.config import ROOT_DIR, DATA_DIR, CONFIG
from aem_mcp.domains.model import DomainProfile, AuditRule

logger = logging.getLogger(__name__)

DOMAINS_ROOT = ROOT_DIR / "domains"


class DomainManager:
    """
    Manages active enterprise domain profiles and exposes a unified
    registry query interface across SQLite, CSV, and JSON formats.
    """

    def __init__(self, domains_dir: Optional[Path] = None):
        self.domains_dir = domains_dir or DOMAINS_ROOT
        self.profiles: Dict[str, DomainProfile] = {}
        self.active_domain_id: str = os.getenv("AEM_DOMAIN", "hospitality").lower().strip()
        self._cached_connections: Dict[str, sqlite3.Connection] = {}
        self._switch_listeners: List[Any] = []
        self.discover_profiles()

    def register_switch_listener(self, callback: Any) -> None:
        """Register a callback that fires whenever the active domain changes."""
        if callback not in self._switch_listeners:
            self._switch_listeners.append(callback)

    def discover_profiles(self) -> None:
        """Scan domains directory and register all domain.yaml configurations (skipping _template and hidden folders)."""
        self.profiles.clear()
        if not self.domains_dir.exists():
            return

        for domain_folder in self.domains_dir.iterdir():
            if not domain_folder.is_dir() or domain_folder.name.startswith(("_", ".")):
                continue
            config_file = domain_folder / "domain.yaml"
            if not config_file.exists():
                continue

            try:
                profile = self._load_profile(domain_folder, config_file)
                self.profiles[profile.id] = profile
            except Exception as e:
                logger.error("Failed loading domain profile from %s: %s", domain_folder, e)

        # Fallback to default if active domain not found
        if self.active_domain_id not in self.profiles and "hospitality" in self.profiles:
            self.active_domain_id = "hospitality"

    def _load_profile(self, folder: Path, config_file: Path) -> DomainProfile:
        with open(config_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        domain_meta = data.get("domain", {})
        entity_meta = data.get("entity", {})
        mapping_meta = data.get("mapping", {})
        audit_meta = data.get("audit_rules", [])

        # Parse audit rules
        rules: List[AuditRule] = []
        for r in audit_meta:
            rules.append(AuditRule(
                id=r.get("id", ""),
                name=r.get("name", ""),
                severity=r.get("severity", "MEDIUM"),
                description=r.get("description", ""),
                rule_type=r.get("rule_type", "cross_reference"),
                condition=r.get("condition"),
                aem_property=r.get("aem_property"),
                registry_column=r.get("registry_column"),
                path_property=r.get("path_property"),
                target_locales=r.get("target_locales", []),
                message=r.get("message", "")
            ))

        # Check for jcr_mock_store.json in folder or fallback to global DATA_DIR
        local_mock = folder / "jcr_mock_store.json"
        if not local_mock.exists():
            local_mock = DATA_DIR / "jcr_mock_store.json"

        profile_id = folder.name.lower().strip()
        data_source_type = entity_meta.get("type", "sqlite").lower()
        data_source_file = entity_meta.get("file", "property_master.sqlite")

        return DomainProfile(
            id=profile_id,
            name=domain_meta.get("name", profile_id.capitalize()),
            description=domain_meta.get("description", ""),
            root_path=domain_meta.get("root_path", f"/content/{profile_id}"),
            primary_entity=entity_meta.get("primary_entity", "Entity"),
            data_source_type=data_source_type,
            data_source_file=data_source_file,
            profile_dir=folder,
            table_descriptions=entity_meta.get("tables", {}),
            field_mappings=mapping_meta.get("fields", {}),
            audit_rules=rules,
            jcr_mock_path=local_mock
        )

    @property
    def active_profile(self) -> DomainProfile:
        if self.active_domain_id not in self.profiles:
            # Create default dummy profile if none discovered yet
            return DomainProfile(
                id="hospitality",
                name="Novaria Hospitality Group",
                description="Default hospitality enterprise demo domain",
                root_path="/content/novaria",
                primary_entity="Hotel",
                data_source_type="sqlite",
                data_source_file="property_master.sqlite",
                profile_dir=self.domains_dir / "hospitality",
                jcr_mock_path=DATA_DIR / "jcr_mock_store.json"
            )
        return self.profiles[self.active_domain_id]

    def switch_domain(self, domain_id: str) -> DomainProfile:
        clean_id = domain_id.lower().strip()
        if clean_id not in self.profiles:
            available = ", ".join(self.profiles.keys())
            raise ValueError(f"Unknown domain '{domain_id}'. Available: {available}")

        self.active_domain_id = clean_id
        profile = self.profiles[clean_id]

        # Update CONFIG paths
        if profile.jcr_mock_path and profile.jcr_mock_path.exists():
            CONFIG.mock_jcr_path = profile.jcr_mock_path
        # Trigger switch listeners
        for listener in self._switch_listeners:
            try:
                listener(profile)
            except Exception as e:
                logger.error("Error in domain switch listener: %s", e)

        return profile

    def get_connection(self) -> sqlite3.Connection:
        """Returns an active SQLite connection for the current domain registry."""
        domain_id = self.active_domain_id
        if domain_id in self._cached_connections:
            return self._cached_connections[domain_id]

        profile = self.active_profile
        source_type = profile.data_source_type
        source_path = profile.data_source_path

        # If file is not in profile dir, check DATA_DIR
        if not source_path.exists() and (DATA_DIR / profile.data_source_file).exists():
            source_path = DATA_DIR / profile.data_source_file

        if source_type == "sqlite":
            if not source_path.exists():
                raise FileNotFoundError(f"SQLite registry not found at {source_path}")
            conn = sqlite3.connect(source_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            self._cached_connections[domain_id] = conn
            return conn

        elif source_type == "csv":
            if not source_path.exists():
                raise FileNotFoundError(f"CSV registry file not found at {source_path}")
            conn = sqlite3.connect(":memory:", check_same_thread=False)
            conn.row_factory = sqlite3.Row
            self._load_csv_into_sqlite(conn, source_path, profile)
            self._cached_connections[domain_id] = conn
            return conn

        elif source_type == "json":
            if not source_path.exists():
                raise FileNotFoundError(f"JSON registry file not found at {source_path}")
            conn = sqlite3.connect(":memory:", check_same_thread=False)
            conn.row_factory = sqlite3.Row
            self._load_json_into_sqlite(conn, source_path, profile)
            self._cached_connections[domain_id] = conn
            return conn

        else:
            raise ValueError(f"Unsupported data source type: {source_type}")

    def _load_csv_into_sqlite(self, conn: sqlite3.Connection, csv_path: Path, profile: DomainProfile) -> None:
        """Loads CSV data into in-memory SQLite tables."""
        table_name = profile.table_descriptions.get("primary_table")
        if not table_name and profile.table_descriptions:
            table_name = list(profile.table_descriptions.keys())[0]
        if not table_name:
            table_name = profile.id

        with open(csv_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            fields = reader.fieldnames or []
            if not fields:
                return

            cols_def = ", ".join([f'"{col}" TEXT' for col in fields])
            conn.execute(f'CREATE TABLE "{table_name}" ({cols_def})')

            placeholders = ", ".join(["?" for _ in fields])
            insert_sql = f'INSERT INTO "{table_name}" VALUES ({placeholders})'

            rows_to_insert = []
            for row in reader:
                rows_to_insert.append([row.get(col, "") for col in fields])

            conn.executemany(insert_sql, rows_to_insert)
            conn.commit()

    def _load_json_into_sqlite(self, conn: sqlite3.Connection, json_path: Path, profile: DomainProfile) -> None:
        """Loads JSON records into in-memory SQLite tables."""
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, list):
            tables = {profile.id: data}
        elif isinstance(data, dict):
            tables = data
        else:
            return

        for table_name, records in tables.items():
            if not records or not isinstance(records, list):
                continue

            first = records[0]
            fields = list(first.keys())
            cols_def = ", ".join([f'"{col}" TEXT' for col in fields])
            conn.execute(f'CREATE TABLE "{table_name}" ({cols_def})')

            placeholders = ", ".join(["?" for _ in fields])
            insert_sql = f'INSERT INTO "{table_name}" VALUES ({placeholders})'

            rows = []
            for item in records:
                row_vals = []
                for fld in fields:
                    val = item.get(fld)
                    if isinstance(val, (dict, list)):
                        val = json.dumps(val)
                    row_vals.append(str(val) if val is not None else "")
                rows.append(row_vals)

            conn.executemany(insert_sql, rows)
            conn.commit()

    def get_indexes(self) -> Dict[str, Any]:
        """Returns available tables/indexes and record counts for active domain."""
        conn = self.get_connection()
        profile = self.active_profile
        
        # Get all table names in the active SQLite connection
        tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        result = {}
        for t in tables:
            t_name = t[0]
            count = conn.execute(f'SELECT COUNT(*) FROM "{t_name}"').fetchone()[0]
            desc = profile.table_descriptions.get(t_name, f"{profile.name} {t_name} entity records")
            result[t_name] = {
                "records": count,
                "description": desc,
                "source": profile.data_source_file,
                "type": profile.data_source_type,
            }
        return result

    def search_registry(
        self,
        index: str,
        query: str = "",
        filters: Optional[Dict[str, str]] = None,
        limit: int = 50
    ) -> Dict[str, Any]:
        """Searches canonical records in the active domain's registry."""
        conn = self.get_connection()
        limit = min(max(int(limit), 1), 500)
        query_str = query.lower().strip()
        filters = filters or {}

        try:
            rows = conn.execute(f'SELECT * FROM "{index}"').fetchall()
        except sqlite3.OperationalError as e:
            raise ValueError(f"Registry table '{index}' not found in domain '{self.active_domain_id}'. {e}")

        matches = []
        for row in rows:
            record = dict(row)
            # Parse JSON columns if present
            for k, v in record.items():
                if isinstance(v, str) and (v.startswith("{") or v.startswith("[")):
                    try:
                        record[k] = json.loads(v)
                    except Exception:
                        pass

            # Text query
            if query_str:
                searchable = " ".join(str(val) for val in record.values()).lower()
                if query_str not in searchable:
                    continue

            # Column-specific filters
            matched_filters = True
            for fk, fv in filters.items():
                if fk in record and str(record[fk]).lower() != str(fv).lower().strip():
                    matched_filters = False
                    break
            if not matched_filters:
                continue

            matches.append(record)

        return {
            "domain": self.active_domain_id,
            "index": index,
            "total": len(matches),
            "results": matches[:limit]
        }

    def get_registry_entity(self, index: str, identifier: str) -> Dict[str, Any]:
        """Looks up a single record in the active domain's registry by ID."""
        conn = self.get_connection()
        target_id = identifier.strip().lower()

        # Try common ID columns
        id_candidates = ["hotel_id", "vin_prefix", "sku", "id", "code", "brand_id", "dealer_id"]
        table_cols = [c[1] for c in conn.execute(f'PRAGMA table_info("{index}")').fetchall()]
        
        chosen_col = None
        for cand in id_candidates:
            if cand in table_cols:
                chosen_col = cand
                break
        if not chosen_col and table_cols:
            chosen_col = table_cols[0]

        row = None
        if chosen_col:
            row = conn.execute(
                f'SELECT * FROM "{index}" WHERE LOWER("{chosen_col}") = ?',
                (target_id,)
            ).fetchone()

        if not row:
            return {
                "domain": self.active_domain_id,
                "index": index,
                "identifier": identifier,
                "found": False
            }

        record = dict(row)
        for k, v in record.items():
            if isinstance(v, str) and (v.startswith("{") or v.startswith("[")):
                try:
                    record[k] = json.loads(v)
                except Exception:
                    pass

        return {
            "domain": self.active_domain_id,
            "index": index,
            "identifier": identifier,
            "found": True,
            "record": record
        }

    def evaluate_audit_rules(self, jcr_engine: Any, limit: int = 100) -> Dict[str, Any]:
        """
        Executes declarative domain governance rules against JCR and Master Registry.
        """
        profile = self.active_profile
        conn = self.get_connection()
        findings: List[Dict[str, Any]] = []

        # 1. Query content pages under profile.root_path
        query_res = jcr_engine.querybuilder({
            "path": profile.root_path,
            "type": "cq:Page",
            "p.limit": "500"
        })
        hits = query_res.get("hits", [])
        
        # Determine primary table name in registry
        primary_table = profile.table_descriptions.get("primary_table")
        if not primary_table and profile.table_descriptions:
            primary_table = list(profile.table_descriptions.keys())[0]
        if not primary_table:
            primary_table = profile.id

        # Index canonical records by primary key
        id_candidates = ["hotel_id", "vin_prefix", "sku", "id", "code"]
        table_cols = [c[1] for c in conn.execute(f'PRAGMA table_info("{primary_table}")').fetchall()]
        chosen_pk = next((c for c in id_candidates if c in table_cols), table_cols[0] if table_cols else None)

        canonical_lookup: Dict[str, Dict[str, Any]] = {}
        if chosen_pk:
            rows = conn.execute(f'SELECT * FROM "{primary_table}"').fetchall()
            for r in rows:
                rec = dict(r)
                val = str(rec.get(chosen_pk, "")).strip().lower()
                if val:
                    canonical_lookup[val] = rec

        # Evaluate rules for each page
        for hit in hits:
            page_path = hit.get("path") or hit.get("jcr:path")
            if not page_path:
                continue
            try:
                page_node = jcr_engine.get_json(page_path, depth=1)
            except KeyError:
                continue
            if not isinstance(page_node, dict):
                continue
            node_data = page_node.get("jcr:content", page_node)
            if not isinstance(node_data, dict):
                continue

            for rule in profile.audit_rules:
                if len(findings) >= limit:
                    break

                # DAM Asset Rule
                if rule.rule_type == "dam_asset" and rule.path_property:
                    parts = rule.path_property.split("/")
                    curr = node_data
                    for p in parts:
                        if isinstance(curr, dict):
                            curr = curr.get(p)
                        else:
                            curr = None
                            break
                    if curr and isinstance(curr, str) and curr.startswith("/content/dam"):
                        is_missing = False
                        try:
                            dam_node = jcr_engine.get_json(curr, depth=0)
                            if not dam_node:
                                is_missing = True
                        except KeyError:
                            is_missing = True

                        if is_missing:
                            findings.append({
                                "rule_id": rule.id,
                                "name": rule.name,
                                "severity": rule.severity,
                                "domain": profile.id,
                                "path": page_path,
                                "referenced_asset": curr,
                                "message": f"{rule.message}: {curr}"
                            })

                # Cross-Reference Rule
                elif rule.rule_type == "cross_reference":
                    aem_id = None
                    for reg_fld, aem_fld in profile.field_mappings.items():
                        if reg_fld == chosen_pk:
                            aem_id = node_data.get(aem_fld)
                            break
                    if not aem_id:
                        for cand in ["vinPrefix", "sku", "hotelId", "id", "entityId"]:
                            if cand in node_data:
                                aem_id = node_data[cand]
                                break

                    if not aem_id:
                        continue

                    canon_rec = canonical_lookup.get(str(aem_id).strip().lower())
                    if not canon_rec:
                        continue

                    is_discrepancy, msg = self._evaluate_rule_condition(
                        condition=rule.condition or "",
                        node_data=node_data,
                        canon_rec=canon_rec,
                        default_msg=rule.message,
                        aem_property=rule.aem_property,
                        registry_column=rule.registry_column,
                    )

                    if is_discrepancy:
                        findings.append({
                            "rule_id": rule.id,
                            "name": rule.name,
                            "severity": rule.severity,
                            "domain": profile.id,
                            "path": page_path,
                            "entity_id": aem_id,
                            "message": msg
                        })

        return {
            "domain": profile.id,
            "domain_name": profile.name,
            "data_source_type": profile.data_source_type,
            "total_findings": len(findings),
            "findings": findings
        }

    @staticmethod
    def _normalize_scalar(val: Any) -> tuple[bool, Any]:
        """
        Normalize a scalar value for comparison.
        Returns (is_empty, normalized_value) where numeric strings like '$89,900' become float(89900.0).
        """
        if val is None:
            return True, None
        if isinstance(val, bool):
            return False, "true" if val else "false"
        if isinstance(val, (int, float)):
            return False, float(val)
        s = str(val).strip()
        if not s:
            return True, ""
        # Strip currency symbols and thousands separators if numeric
        cleaned = s.replace("$", "").replace(",", "").strip()
        try:
            return False, float(cleaned)
        except ValueError:
            return False, s.lower()

    @classmethod
    def _compare_values(cls, left_raw: Any, right_raw: Any, op: ast.cmpop) -> bool:
        left_empty, left_val = cls._normalize_scalar(left_raw)
        right_empty, right_val = cls._normalize_scalar(right_raw)

        # If either side is missing/empty (unauthored property), do not flag a discrepancy
        # unless comparing two empty values for equality
        if left_empty or right_empty:
            if left_empty and right_empty:
                return isinstance(op, ast.Eq)
            return False

        # If one is float and the other is string (e.g. 'true'), compare as lowercased strings
        if isinstance(left_val, float) != isinstance(right_val, float):
            left_val = str(left_raw).strip().lower()
            right_val = str(right_raw).strip().lower()

        if isinstance(op, ast.Eq):
            return left_val == right_val
        if isinstance(op, ast.NotEq):
            return left_val != right_val
        if isinstance(op, ast.Lt):
            return left_val < right_val
        if isinstance(op, ast.LtE):
            return left_val <= right_val
        if isinstance(op, ast.Gt):
            return left_val > right_val
        if isinstance(op, ast.GtE):
            return left_val >= right_val
        return False

    @classmethod
    def _evaluate_rule_condition(
        cls,
        condition: str,
        node_data: Dict[str, Any],
        canon_rec: Dict[str, Any],
        default_msg: str = "",
        aem_property: Optional[str] = None,
        registry_column: Optional[str] = None,
    ) -> tuple[bool, str]:
        """
        Safely parses and evaluates a declarative governance condition from domain.yaml
        using Python's AST (mode='eval') without eval().
        Supports expressions like:
          - "aem.msrp != registry.base_msrp"
          - "registry.status == 'DISCONTINUED' and aem.status == 'ACTIVE'"
          - "aem.price < registry.min_price or aem.discount > 50"
        Also supports simple property mapping fallback when only aem_property + registry_column are set.
        """
        expr_str = condition.strip()
        if not expr_str and aem_property and registry_column:
            expr_str = f"aem.{aem_property} != registry.{registry_column}"
        if not expr_str:
            return False, default_msg

        observed: Dict[str, Any] = {}

        def _resolve_node(node: ast.AST) -> Any:
            if isinstance(node, ast.Constant):
                return node.value
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                ns = node.value.id
                attr = node.attr
                if ns == "aem":
                    val = node_data.get(attr)
                    observed[f"aem.{attr}"] = val
                    return val
                if ns == "registry":
                    val = canon_rec.get(attr)
                    observed[f"registry.{attr}"] = val
                    return val
                raise ValueError(f"Unsupported namespace '{ns}' in rule condition")
            if isinstance(node, ast.BoolOp):
                if isinstance(node.op, ast.And):
                    return all(bool(_resolve_node(v)) for v in node.values)
                if isinstance(node.op, ast.Or):
                    return any(bool(_resolve_node(v)) for v in node.values)
                raise ValueError("Unsupported boolean operator")
            if isinstance(node, ast.UnaryOp):
                if isinstance(node.op, ast.Not):
                    return not bool(_resolve_node(node.operand))
                if isinstance(node.op, ast.USub):
                    operand = _resolve_node(node.operand)
                    return -operand if isinstance(operand, (int, float)) else None
                raise ValueError("Unsupported unary operator")
            if isinstance(node, ast.Compare):
                left = _resolve_node(node.left)
                for op, comparator in zip(node.ops, node.comparators):
                    right = _resolve_node(comparator)
                    if not cls._compare_values(left, right, op):
                        return False
                    left = right
                return True
            raise ValueError(f"Unsupported AST node '{type(node).__name__}' in rule condition")

        try:
            parsed = ast.parse(expr_str, mode="eval")
            triggered = bool(_resolve_node(parsed.body))
        except Exception as e:
            logger.warning("Failed evaluating audit rule condition '%s': %s", expr_str, e)
            return False, default_msg

        if not triggered:
            return False, default_msg

        details = ", ".join(f"{k}={v!r}" for k, v in observed.items())
        msg = f"{default_msg} ({details})" if (default_msg and details) else (default_msg or details)
        return True, msg


DOMAIN_MANAGER = DomainManager()
