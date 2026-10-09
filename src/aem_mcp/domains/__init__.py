"""
Domain management and pluggable profiles for AEM Content Intelligence MCP.
"""

from aem_mcp.domains.model import DomainProfile, AuditRule
from aem_mcp.domains.manager import DomainManager, DOMAIN_MANAGER

__all__ = ["DomainProfile", "AuditRule", "DomainManager", "DOMAIN_MANAGER"]
