"""
Virtual JCR Simulator for AEM Content Intelligence Starter Kit.

Simulates:
1. Sling JSON selector resolution (.json, .1.json, .2.json)
2. AEM QueryBuilder predicate evaluation (/bin/querybuilder.json)
3. Repository path-reference discovery
"""

import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent.parent
DEFAULT_DATA_PATH = ROOT / "data" / "jcr_mock_store.json"


class JcrEngine:
    """In-memory JCR simulator replicating Sling GET and QueryBuilder endpoints."""

    def __init__(self, data_path: Path | str | None = None):
        path = Path(data_path) if data_path else DEFAULT_DATA_PATH
        if not path.exists():
            raise FileNotFoundError(
                f"Synthetic JCR store not found at {path}. Run tools/generate_dataset.py first."
            )
        with open(path, "r", encoding="utf-8") as f:
            self._nodes: dict[str, dict[str, Any]] = json.load(f)

    @property
    def total_nodes(self) -> int:
        return len(self._nodes)

    def get_node(self, path: str) -> dict[str, Any] | None:
        clean_path = path.rstrip("/")
        return self._nodes.get(clean_path)

    def get_json(self, path: str, depth: int = 0) -> dict[str, Any]:
        """
        Simulates AEM Sling GET servlet: path.{depth}.json
        depth=0: returns direct properties and direct child names
        depth=1..3: recursively populates child node objects
        """
        clean_path = path.removesuffix(".json")
        depth = min(max(int(depth), 0), 3)

        # Look for explicit depth in selector like path.1.json
        depth_match = re.search(r"\.(\d+)$", clean_path)
        if depth_match:
            depth = min(int(depth_match.group(1)), 3)
            clean_path = clean_path[:depth_match.start()]

        clean_path = clean_path.rstrip("/")
        if not clean_path.startswith("/"):
            clean_path = "/" + clean_path

        if clean_path not in self._nodes:
            raise KeyError(f"Resource not found at path: {clean_path}")

        return self._build_view(clean_path, depth)

    def _build_view(self, path: str, depth: int) -> dict[str, Any]:
        node_data = self._nodes[path]
        result: dict[str, Any] = {}

        # Copy non-dict properties (JCR properties)
        for key, value in node_data.items():
            if not isinstance(value, dict) or key in {"jcr:content", "metadata", "data"}:
                result[key] = value

        # Discover child nodes stored directly under this path in the flat store
        child_paths = [
            p for p in self._nodes
            if p != path and p.startswith(path + "/") and "/" not in p[len(path) + 1:]
        ]

        if depth > 0:
            for child_path in child_paths:
                child_name = child_path.split("/")[-1]
                result[child_name] = self._build_view(child_path, depth - 1)
        else:
            for child_path in child_paths:
                child_name = child_path.split("/")[-1]
                child_type = self._nodes[child_path].get("jcr:primaryType", "nt:base")
                result[child_name] = {"jcr:primaryType": child_type}

        return result

    def querybuilder(self, query: dict[str, Any]) -> dict[str, Any]:
        """
        Simulates /bin/querybuilder.json with common predicates:
        - path: root search path
        - type: jcr:primaryType filter
        - fulltext: text search across all string attributes
        - property / {n}_property: property name
        - property.value / {n}_property.value: property match value
        - property.operation: 'equals', 'unequals', 'exists', 'not'
        - p.limit: page size (default 20, max 1000)
        - p.offset: pagination offset (default 0)
        """
        search_path = query.get("path", "/content").rstrip("/")
        node_type = query.get("type")
        fulltext = query.get("fulltext", "").lower().strip()

        limit = min(int(query.get("p.limit", 20)), 1000)
        offset = max(int(query.get("p.offset", 0)), 0)

        # Collect property filters
        property_filters = []
        if "property" in query:
            property_filters.append((
                query.get("property"),
                query.get("property.value"),
                query.get("property.operation", "equals")
            ))

        for key, val in query.items():
            prop_match = re.match(r"^(\d+)_property$", key)
            if prop_match:
                prefix = prop_match.group(1)
                prop_val = query.get(f"{prefix}_property.value")
                prop_op = query.get(f"{prefix}_property.operation", "equals")
                property_filters.append((val, prop_val, prop_op))

        hits = []

        for path, node in self._nodes.items():
            # 1. Path match
            if not (path == search_path or path.startswith(search_path + "/")):
                continue

            # 2. Type match (check node itself or child jcr:content)
            if node_type:
                primary_type = node.get("jcr:primaryType", "")
                content_type = node.get("jcr:content", {}).get("jcr:primaryType", "")
                if node_type not in {primary_type, content_type}:
                    continue

            # 3. Property filters
            matched_props = True
            for prop_name, prop_val, prop_op in property_filters:
                found_val = self._resolve_property(node, prop_name)
                if prop_op == "exists":
                    if found_val is None:
                        matched_props = False
                        break
                elif prop_op == "not":
                    if found_val is not None:
                        matched_props = False
                        break
                elif prop_op == "unequals":
                    if str(found_val).lower() == str(prop_val).lower():
                        matched_props = False
                        break
                else:  # equals
                    if prop_val is not None and str(found_val).lower() != str(prop_val).lower():
                        matched_props = False
                        break

            if not matched_props:
                continue

            # 4. Full-text search
            if fulltext:
                text_chunks = [json.dumps(node, ensure_ascii=False)]
                for p, n in self._nodes.items():
                    if p.startswith(path + "/"):
                        text_chunks.append(json.dumps(n, ensure_ascii=False))
                combined_text = " ".join(text_chunks).lower()
                if fulltext not in combined_text:
                    continue

            # Include hit
            hit_data = {
                "jcr:path": path,
                "jcr:primaryType": node.get("jcr:primaryType", "nt:unstructured"),
            }
            if "jcr:content" in node and isinstance(node["jcr:content"], dict):
                hit_data["jcr:title"] = node["jcr:content"].get("jcr:title", "")
                hit_data["sling:resourceType"] = node["jcr:content"].get("sling:resourceType", "")
                if "hotelId" in node["jcr:content"]:
                    hit_data["hotelId"] = node["jcr:content"]["hotelId"]

            hits.append(hit_data)

        total = len(hits)
        paged_hits = hits[offset : offset + limit]

        return {
            "success": True,
            "results": len(paged_hits),
            "total": total,
            "offset": offset,
            "limit": limit,
            "hits": paged_hits,
        }

    def _resolve_property(self, node: dict[str, Any], prop_path: str) -> Any:
        """Resolve property like 'hotelId' or 'jcr:content/hotelId' or 'data/master/hotelId'."""
        if prop_path in node:
            return node[prop_path]
        if "jcr:content" in node and isinstance(node["jcr:content"], dict):
            if prop_path in node["jcr:content"]:
                return node["jcr:content"][prop_path]
            if "data" in node["jcr:content"] and isinstance(node["jcr:content"]["data"], dict):
                master = node["jcr:content"]["data"].get("master", {})
                if prop_path in master:
                    return master[prop_path]
        return None

    def find_references(self, path: str) -> list[str]:
        """Extract all repository paths referenced by the node and its descendant content."""
        clean_path = path.removesuffix(".json").rstrip("/")
        if clean_path not in self._nodes:
            raise KeyError(f"Node not found: {clean_path}")

        nodes_to_scan = [self._nodes[clean_path]]
        for p, n in self._nodes.items():
            if p.startswith(clean_path + "/"):
                nodes_to_scan.append(n)

        serialized = json.dumps(nodes_to_scan, ensure_ascii=False)
        refs = sorted(set(
            re.findall(r"/(?:content|conf|etc|apps|libs)/[^\s\"\\,}]+", serialized)
        ))
        # Filter out self and subpaths
        return [r for r in refs if r != clean_path and not r.startswith(clean_path + "/")]

