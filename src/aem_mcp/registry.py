"""
Master Entity Registry interface for AEM Content Intelligence MCP.

Production-grade, decoupled enterprise hospitality data store
for Meridian Hospitality Group.
"""

import json
import sqlite3
from typing import Any
from aem_mcp.config import CONFIG


def _db_connection() -> sqlite3.Connection:
    if not CONFIG.property_master_db.exists():
        raise FileNotFoundError(
            f"Property Master database not found at {CONFIG.property_master_db}. "
            "Run tools/generate_dataset.py first."
        )
    conn = sqlite3.connect(CONFIG.property_master_db)
    conn.row_factory = sqlite3.Row
    return conn


INDEX_DESCRIPTIONS = {
    "properties": "Canonical hotel and resort entity records (hotel_id, brand_id, amenities, rooms, GPS, star rating)",
    "brands": "Brand portfolio definitions, tiers, and operational brand standards",
    "destinations": "Global destination metadata, coordinates, country and region codes",
}


def get_registry_indexes() -> dict[str, Any]:
    """List available canonical business entity indexes and record counts."""
    result = {}
    with _db_connection() as conn:
        for index_name, desc in INDEX_DESCRIPTIONS.items():
            count = conn.execute(f"SELECT COUNT(*) FROM {index_name}").fetchone()[0]
            result[index_name] = {
                "records": count,
                "description": desc,
                "source": "property_master.sqlite"
            }
    return result


def search_registry(
    index: str,
    query: str = "",
    city: str = "",
    brand: str = "",
    limit: int = 50
) -> dict[str, Any]:
    """Search canonical records using structured filters."""
    if index not in INDEX_DESCRIPTIONS:
        valid = ", ".join(INDEX_DESCRIPTIONS)
        raise ValueError(f"Unsupported registry index '{index}'. Use one of: {valid}.")

    limit = min(max(int(limit), 1), 500)
    query_str = query.lower().strip()
    city_str = city.lower().strip()
    brand_str = brand.lower().strip()

    with _db_connection() as conn:
        rows = conn.execute(f"SELECT * FROM {index}").fetchall()

    matches = []
    for row in rows:
        record = dict(row)
        # Parse JSON fields if present
        for json_col in ("standards", "amenities"):
            if json_col in record and isinstance(record[json_col], str):
                try:
                    record[json_col] = json.loads(record[json_col])
                except Exception:
                    pass

        # Text search across string representations
        if query_str:
            searchable = " ".join(str(v) for v in record.values()).lower()
            if query_str not in searchable:
                continue

        # Structured filters for properties
        if index == "properties":
            if city_str and str(record.get("city", "")).lower() != city_str:
                continue
            if brand_str and str(record.get("brand_id", "")).lower() != brand_str:
                continue

        matches.append(record)

    return {
        "index": index,
        "total": len(matches),
        "results": matches[:limit]
    }


def get_registry_entity(index: str, identifier: str) -> dict[str, Any]:
    """Retrieve one canonical record by primary identifier."""
    if index not in INDEX_DESCRIPTIONS:
        valid = ", ".join(INDEX_DESCRIPTIONS)
        raise ValueError(f"Unsupported registry index '{index}'. Use one of: {valid}.")

    target_id = identifier.strip().lower()

    with _db_connection() as conn:
        if index == "brands":
            row = conn.execute(
                "SELECT * FROM brands WHERE LOWER(brand_id) = ?", (target_id,)
            ).fetchone()
        elif index == "destinations":
            row = conn.execute(
                "SELECT * FROM destinations WHERE LOWER(code) = ? OR LOWER(city) = ?",
                (target_id, target_id)
            ).fetchone()
        else:  # properties
            row = conn.execute(
                "SELECT * FROM properties WHERE LOWER(hotel_id) = ? OR LOWER(slug) = ?",
                (target_id, target_id)
            ).fetchone()

    if not row:
        return {"index": index, "identifier": identifier, "found": False}

    record = dict(row)
    for json_col in ("standards", "amenities"):
        if json_col in record and isinstance(record[json_col], str):
            try:
                record[json_col] = json.loads(record[json_col])
            except Exception:
                pass

    return {"index": index, "identifier": identifier, "found": True, "record": record}
