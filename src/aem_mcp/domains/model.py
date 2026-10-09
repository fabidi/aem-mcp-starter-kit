"""
Domain profile data models for AEM Content Intelligence MCP.
Defines declarative domain schemas, field mappings, and governance audit rules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class AuditRule:
    """Represents a declarative content governance or discrepancy audit rule."""
    id: str
    name: str
    severity: str  # CRITICAL, HIGH, MEDIUM, LOW
    description: str
    rule_type: str  # cross_reference, dam_asset, localized_copy, property_constraint
    condition: Optional[str] = None
    aem_property: Optional[str] = None
    registry_column: Optional[str] = None
    path_property: Optional[str] = None
    target_locales: List[str] = field(default_factory=list)
    message: str = ""


@dataclass
class DomainProfile:
    """Represents a bundled enterprise domain profile (e.g. Hospitality, Automotive, Retail)."""
    id: str
    name: str
    description: str
    root_path: str
    primary_entity: str
    data_source_type: str  # sqlite, csv, json
    data_source_file: str
    profile_dir: Path
    table_descriptions: Dict[str, str] = field(default_factory=dict)
    field_mappings: Dict[str, str] = field(default_factory=dict)  # registry_field -> aem_property
    audit_rules: List[AuditRule] = field(default_factory=list)
    jcr_mock_path: Optional[Path] = None

    @property
    def data_source_path(self) -> Path:
        return self.profile_dir / self.data_source_file
