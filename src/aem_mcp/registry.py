"""
Master Entity Registry interface for AEM Content Intelligence MCP.

Unified, pluggable enterprise data store supporting multiple domain profiles:
- Hospitality (Novaria: SQLite)
- Automotive (Apex: CSV)
- Retail (Lumina: JSON)
"""

from __future__ import annotations

import sqlite3
from typing import Any, Dict

from aem_mcp.domains.manager import DOMAIN_MANAGER


def _db_connection() -> sqlite3.Connection:
    return DOMAIN_MANAGER.get_connection()


@property
def INDEX_DESCRIPTIONS() -> Dict[str, str]:
    return DOMAIN_MANAGER.active_profile.table_descriptions


def get_registry_indexes() -> dict[str, Any]:
    """List available canonical business entity indexes and record counts for active domain."""
    return DOMAIN_MANAGER.get_indexes()


def search_registry(
    index: str,
    query: str = "",
    city: str = "",
    brand: str = "",
    limit: int = 50,
    **kwargs: Any
) -> dict[str, Any]:
    """Search canonical records in active domain using structured filters."""
    filters = {}
    if city:
        filters["city"] = city
    if brand:
        filters["brand_id"] = brand
    for k, v in kwargs.items():
        if v:
            filters[k] = v

    return DOMAIN_MANAGER.search_registry(
        index=index,
        query=query,
        filters=filters,
        limit=limit
    )


def get_registry_entity(index: str, identifier: str) -> dict[str, Any]:
    """Retrieve one canonical record by primary identifier in active domain."""
    return DOMAIN_MANAGER.get_registry_entity(index=index, identifier=identifier)
