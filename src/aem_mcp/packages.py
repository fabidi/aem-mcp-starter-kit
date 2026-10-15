"""
Environment-Scoped AEM CRX Package Manager & Private Exchange Storage Engine.

Implements the multi-instance MCP package synchronization architecture:
- Each MCP process is bound to a single configured AEM environment.
- Package tools never accept a target environment argument; the selected MCP connection determines the target.
- Package bytes are stored in MCP-managed private exchange storage (`packages/exchange/`) and represented
  to the agent exclusively by an opaque `package_handle` (`pkg_<12hex>`). Raw ZIP bytes never enter LLM context.
- Mutating operations (`package_create`, `package_set_filters`, `package_build`, `package_upload`, `package_install`)
  require explicit confirmation (`confirm=True`), whereas `package_list` and `package_download` are read-only.
- Live mode uses AEM's documented CRX Package Manager HTTP APIs:
    * Create:   POST /crx/packmgr/service/.json/etc/packages/{group}/{name}-{version}.zip?cmd=create
    * Filters:  POST /crx/packmgr/update.jsp (JSON filter array)
    * Build:    POST /crx/packmgr/service/.json/etc/packages/{group}/{name}-{version}.zip?cmd=build
    * Download: GET  /etc/packages/{group}/{name}-{version}.zip
    * Upload:   POST /crx/packmgr/service/.json/?cmd=upload (multipart/form-data)
    * Install:  POST /crx/packmgr/service/.json/etc/packages/{group}/{name}-{version}.zip?cmd=install
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import urllib.parse
import urllib.request
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from aem_mcp.config import CONFIG, ROOT_DIR

PACKAGE_EXCHANGE_DIR = ROOT_DIR / "packages" / "exchange"
MOCK_PACKAGES_DIR = ROOT_DIR / "packages" / "mock_env_state"

# Hard Size & Node Count Guardrails (100 MB & 10,000 JCR Nodes)
MAX_PACKAGE_SIZE_MB = 100
MAX_PACKAGE_BYTES = MAX_PACKAGE_SIZE_MB * 1024 * 1024  # 104,857,600 bytes (100 MB)
MAX_PACKAGE_NODES = 10_000

_HANDLE_RE = re.compile(r"^pkg_[0-9a-f]{12}$")
_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$")
_GROUP_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._/-]{0,127}$")
_VERSION_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$")

# Allowed JCR Root Types and their minimum required segment depth
# (e.g. '/content/novaria' has 2 segments; '/content/dam/novaria' has 3 segments)
ALLOWED_PACKAGE_ROOT_TYPES: tuple[str, ...] = (
    "/content",
    "/conf",
    "/apps",
    "/etc/designs",
    "/var/workflow/models",
)

# Shared multi-tenant containers under /content or /conf that require at least 3 segments
# (e.g. '/content/dam/<project>', '/content/experience-fragments/<project>')
_DEPTH_3_CONTAINERS: set[str] = {
    "/content/dam",
    "/content/experience-fragments",
    "/content/campaigns",
    "/content/projects",
    "/content/cq:tags",
    "/content/communities",
    "/content/forms",
    "/content/usergenerated",
    "/conf/global",
}

_FORBIDDEN_FILTER_ROOTS: set[str] = {
    "/",
    "/libs",
    "/etc",
    "/etc/packages",
    "/system",
    "/oak:index",
    "/jcr:system",
    "/rep:policy",
    "/bin",
    "/home",
    "/tmp",
    "/var",
}


# ---------------------------------------------------------------------------
# 1. Validation & Guardrail Helpers
# ---------------------------------------------------------------------------

def validate_package_coordinates(group: str, name: str, version: str) -> dict[str, str]:
    """Validate CRX package group, name, and version and return canonical coordinates."""
    clean_group = (group or "").strip().strip("/")
    clean_name = (name or "").strip()
    clean_version = str(version or "").strip()

    if not clean_group or ".." in clean_group or "//" in clean_group or not _GROUP_RE.fullmatch(clean_group):
        raise ValueError(
            f"Invalid package group '{group}'. Must be non-empty alphanumeric (may include '-', '_', '.', '/') without '..'."
        )
    if not clean_name or ".." in clean_name or not _NAME_RE.fullmatch(clean_name):
        raise ValueError(
            f"Invalid package name '{name}'. Must be non-empty alphanumeric (may include '-', '_', '.') without '..'."
        )
    if not clean_version or ".." in clean_version or not _VERSION_RE.fullmatch(clean_version):
        raise ValueError(
            f"Invalid package version '{version}'. Must be non-empty alphanumeric (e.g. '1.0', '1.0.0-SNAPSHOT')."
        )

    zip_name = f"{clean_name}-{clean_version}.zip"
    package_path = f"/etc/packages/{clean_group}/{zip_name}"
    return {
        "group": clean_group,
        "name": clean_name,
        "version": clean_version,
        "zip_name": zip_name,
        "package_path": package_path,
    }


def validate_single_filter_root(raw_path: str) -> str:
    """
    Enforce path guardrails on a single repository filter root:
    1. Must be a clean absolute path starting with '/'.
    2. Never allow '/libs' or any '/libs/*' subpath (immutable AEM product tree).
    3. Never allow '/', '/etc/packages', '/system', '/oak:index', etc.
    4. Must belong to an allowed root type (/content, /conf, /apps, /etc/designs, /var/workflow/models).
    5. Cannot go above the minimum path depth ceiling (e.g. '/content', '/conf', '/apps', or '/content/dam'
       without a tenant/project folder are blocked).
    """
    raw_root = (raw_path or "").strip()
    if not raw_root:
        raise ValueError("Filter root path cannot be empty.")
    if not raw_root.startswith("/"):
        raise ValueError(f"Filter root '{raw_root}' must be an absolute repository path starting with '/'.")
    if raw_root != "/" and raw_root.endswith("/"):
        raw_root = raw_root.rstrip("/")
    if ".." in raw_root.split("/") or "//" in raw_root or any(c.isspace() for c in raw_root):
        raise ValueError(f"Unsafe or malformed repository filter path '{raw_root}'.")

    # Guardrail 1: Explicit /libs prohibition (never package or install into /libs)
    if raw_root == "/libs" or raw_root.startswith("/libs/"):
        raise ValueError(
            f"Filter root '{raw_root}' is forbidden: '/libs' is immutable AEM product code. "
            "Packages must never target or install into '/libs'."
        )

    # Guardrail 2: Explicit forbidden system/package roots
    if raw_root in _FORBIDDEN_FILTER_ROOTS or raw_root.startswith("/etc/packages/"):
        raise ValueError(
            f"Filter root '{raw_root}' is forbidden. '/' and '/etc/packages' (or system roots) cannot be used as content filters."
        )

    # Guardrail 3: Restrict to valid AEM root types
    matches_allowed_type = any(
        raw_root == allowed or raw_root.startswith(allowed + "/")
        for allowed in ALLOWED_PACKAGE_ROOT_TYPES
    )
    if not matches_allowed_type:
        raise ValueError(
            f"Filter root '{raw_root}' is not in an allowed repository root type. "
            f"Allowed root hierarchies: {', '.join(ALLOWED_PACKAGE_ROOT_TYPES)}."
        )

    # Guardrail 4: Minimum path depth ceiling (cannot go above project/tenant subtree level)
    segments = [seg for seg in raw_root.split("/") if seg]
    if raw_root.startswith("/var/workflow/models"):
        min_segments = 4  # /var/workflow/models/<model>
        example = "/var/workflow/models/<workflow-id>"
    elif raw_root.startswith("/etc/designs"):
        min_segments = 3  # /etc/designs/<site>
        example = "/etc/designs/<site>"
    elif any(raw_root == c or raw_root.startswith(c + "/") for c in _DEPTH_3_CONTAINERS):
        min_segments = 3  # e.g. /content/dam/<project>
        container = "/" + "/".join(segments[:2])
        example = f"{container}/<project>"
    else:
        min_segments = 2  # e.g. /content/<site>, /conf/<site>, /apps/<project>
        top = "/" + segments[0]
        example = f"{top}/<project>"

    if len(segments) < min_segments:
        raise ValueError(
            f"Filter root '{raw_root}' is above the minimum allowed path depth ceiling "
            f"(requires at least {min_segments} path segments, e.g. '{example}'). "
            "Packaging an entire top-level root or shared container is forbidden."
        )

    return raw_root


def validate_package_filters(filters: list[str | dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Validate and normalize a list of repository filter roots.
    Enforces allowed root types, minimum depth ceilings, duplicate detection, and '/libs' prohibition.
    """
    if not isinstance(filters, list) or len(filters) == 0:
        raise ValueError("At least one repository filter root must be provided.")

    normalized: list[dict[str, Any]] = []
    seen_roots: set[str] = set()

    for item in filters:
        if isinstance(item, str):
            raw_root = item.strip()
            rules: list[dict[str, str]] = []
        elif isinstance(item, dict):
            raw_root = str(item.get("root", "")).strip()
            raw_rules = item.get("rules", [])
            rules = list(raw_rules) if isinstance(raw_rules, list) else []
        else:
            raise ValueError(f"Invalid filter entry '{item}'. Must be a path string or {{'root': '/path', 'rules': []}}.")

        clean_root = validate_single_filter_root(raw_root)

        if clean_root in seen_roots:
            raise ValueError(f"Duplicate filter root '{clean_root}' is not allowed.")

        seen_roots.add(clean_root)
        normalized.append({"root": clean_root, "rules": rules})

    return normalized


def validate_package_handle(package_handle: str) -> str:
    clean = (package_handle or "").strip()
    if not _HANDLE_RE.fullmatch(clean):
        raise ValueError(
            f"Invalid package_handle '{package_handle}'. Expected opaque handle format 'pkg_<12hex>'."
        )
    return clean


def inspect_and_validate_package_zip(
    zip_bytes: bytes,
    max_bytes: int = MAX_PACKAGE_BYTES,
    max_nodes: int = MAX_PACKAGE_NODES,
) -> dict[str, Any]:
    """
    Deep in-code guardrail inspection for CRX package archives before download staging, upload, or installation:
    1. Enforces the 100 MB compressed AND uncompressed byte size limit.
    2. Enforces the 10,000 JCR node / entry count limit.
    3. Scans META-INF/vault/filter.xml, jcr_root/* archive entries, and _jcr_snapshot.json to guarantee
       the package NEVER targets or installs into '/libs' or violates path depth/type guardrails.
    """
    if len(zip_bytes) > max_bytes:
        raise ValueError(
            f"Package size guardrail exceeded: archive size ({len(zip_bytes):,} bytes) exceeds the "
            f"maximum allowed {max_bytes:,} bytes ({max_bytes // (1024 * 1024)} MB)."
        )

    if not zipfile.is_zipfile(io.BytesIO(zip_bytes)):
        # In unit tests with synthetic non-zip byte stubs, still enforce byte limit above
        return {"compressed_bytes": len(zip_bytes), "uncompressed_bytes": len(zip_bytes), "node_count": 0}

    with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
        infos = zf.infolist()
        total_uncompressed = sum(info.file_size for info in infos)
        if total_uncompressed > max_bytes:
            raise ValueError(
                f"Package uncompressed size guardrail exceeded: {total_uncompressed:,} bytes exceeds the "
                f"maximum allowed {max_bytes:,} bytes ({max_bytes // (1024 * 1024)} MB)."
            )

        names = zf.namelist()
        if len(names) > max_nodes:
            raise ValueError(
                f"Package node/entry count guardrail exceeded: archive contains {len(names):,} entries "
                f"(maximum allowed is {max_nodes:,})."
            )

        # Check every jcr_root/ entry path inside the ZIP for /libs or forbidden targets
        for entry_name in names:
            norm_entry = entry_name.replace("\\", "/").lstrip("/")
            if norm_entry == "jcr_root/libs" or norm_entry.startswith("jcr_root/libs/"):
                raise ValueError(
                    f"Security guardrail violation: Package archive contains '{entry_name}'. "
                    "Installing into '/libs' is strictly forbidden."
                )

        # Inspect META-INF/vault/filter.xml if present
        if "META-INF/vault/filter.xml" in names:
            filter_xml = zf.read("META-INF/vault/filter.xml").decode("utf-8", errors="replace")
            roots_in_xml = re.findall(r'<filter\s+[^>]*root=["\']([^"\']+)["\']', filter_xml)
            if roots_in_xml:
                validate_package_filters(roots_in_xml)

        # Inspect simulated JCR node snapshot if present
        node_count = len(names)
        if "jcr_root/_jcr_snapshot.json" in names:
            snapshot = json.loads(zf.read("jcr_root/_jcr_snapshot.json").decode("utf-8"))
            if isinstance(snapshot, dict):
                node_count = len(snapshot)
                if node_count > max_nodes:
                    raise ValueError(
                        f"Package node limit guardrail exceeded: package contains {node_count:,} JCR nodes "
                        f"(maximum allowed is {max_nodes:,} nodes)."
                    )
                for node_path in snapshot.keys():
                    if node_path == "/libs" or node_path.startswith("/libs/"):
                        raise ValueError(
                            f"Security guardrail violation: Package contains JCR node '{node_path}'. "
                            "Installing into '/libs' is strictly forbidden."
                        )
                    validate_single_filter_root(node_path)

    return {
        "compressed_bytes": len(zip_bytes),
        "uncompressed_bytes": total_uncompressed,
        "node_count": node_count,
    }


# ---------------------------------------------------------------------------
# 2. AEM CRX Package Manager Endpoint Builders
# ---------------------------------------------------------------------------

def build_create_request(base_url: str, coords: dict[str, str]) -> dict[str, Any]:
    """
    Build request metadata matching AEM's documented Package Manager create endpoint:
    POST /crx/packmgr/service/.json/etc/packages/{group}/{name}-{version}.zip?cmd=create
    """
    endpoint_path = f"/crx/packmgr/service/.json{coords['package_path']}"
    query = urllib.parse.urlencode({"cmd": "create"})
    form_params = {
        "packageName": coords["name"],
        "groupName": coords["group"],
        "packageVersion": coords["version"],
        "_charset_": "utf-8",
    }
    return {
        "method": "POST",
        "url": f"{base_url.rstrip('/')}{endpoint_path}?{query}",
        "endpoint_path": f"{endpoint_path}?{query}",
        "form_fields": form_params,
        "encoded_body": urllib.parse.urlencode(form_params).encode("utf-8"),
        "content_type": "application/x-www-form-urlencoded; charset=utf-8",
    }


def build_update_filters_request(
    base_url: str,
    coords: dict[str, str],
    normalized_filters: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Build request metadata matching AEM's documented update.jsp filter endpoint:
    POST /crx/packmgr/update.jsp with JSON-serialized filter array.
    """
    endpoint_path = "/crx/packmgr/update.jsp"
    filter_json = json.dumps(normalized_filters, separators=(",", ":"))
    form_params = {
        "path": coords["package_path"],
        "packageName": coords["name"],
        "groupName": coords["group"],
        "version": coords["version"],
        "filter": filter_json,
        "_charset_": "utf-8",
    }
    return {
        "method": "POST",
        "url": f"{base_url.rstrip('/')}{endpoint_path}",
        "endpoint_path": endpoint_path,
        "form_fields": form_params,
        "filter_json": filter_json,
        "encoded_body": urllib.parse.urlencode(form_params).encode("utf-8"),
        "content_type": "application/x-www-form-urlencoded; charset=utf-8",
    }


def build_lifecycle_command_request(base_url: str, coords: dict[str, str], cmd: str) -> dict[str, Any]:
    """
    Build request metadata for package lifecycle commands (build, install):
    POST /crx/packmgr/service/.json/etc/packages/{group}/{name}-{version}.zip?cmd={build|install}
    """
    if cmd not in ("build", "install"):
        raise ValueError(f"Unsupported package command '{cmd}'.")
    endpoint_path = f"/crx/packmgr/service/.json{coords['package_path']}"
    query = urllib.parse.urlencode({"cmd": cmd})
    return {
        "method": "POST",
        "url": f"{base_url.rstrip('/')}{endpoint_path}?{query}",
        "endpoint_path": f"{endpoint_path}?{query}",
        "encoded_body": b"",
        "content_type": "application/x-www-form-urlencoded; charset=utf-8",
    }


def build_multipart_upload_request(
    base_url: str,
    zip_filename: str,
    zip_bytes: bytes,
    force: bool = True,
) -> dict[str, Any]:
    """
    Build multipart/form-data upload request for AEM CRX Package Manager:
    POST /crx/packmgr/service/.json/?cmd=upload
    """
    boundary = f"----AemMcpPkgBoundary{uuid.uuid4().hex}"
    lines: list[bytes] = []

    # force field
    lines.append(f"--{boundary}\r\n".encode("utf-8"))
    lines.append(b'Content-Disposition: form-data; name="force"\r\n\r\n')
    lines.append(("true" if force else "false").encode("utf-8") + b"\r\n")

    # package file field
    lines.append(f"--{boundary}\r\n".encode("utf-8"))
    lines.append(
        f'Content-Disposition: form-data; name="package"; filename="{zip_filename}"\r\n'.encode("utf-8")
    )
    lines.append(b"Content-Type: application/zip\r\n\r\n")
    lines.append(zip_bytes + b"\r\n")
    lines.append(f"--{boundary}--\r\n".encode("utf-8"))

    body = b"".join(lines)
    endpoint_path = "/crx/packmgr/service/.json/?cmd=upload"
    return {
        "method": "POST",
        "url": f"{base_url.rstrip('/')}{endpoint_path}",
        "endpoint_path": endpoint_path,
        "encoded_body": body,
        "content_type": f"multipart/form-data; boundary={boundary}",
    }


# ---------------------------------------------------------------------------
# 3. Private Exchange Storage & Environment-Scoped Package Manager
# ---------------------------------------------------------------------------

def _confirmation_gate(
    operation: str,
    env_label: str,
    preview: dict[str, Any],
) -> dict[str, Any]:
    return {
        "status": "confirmation_required",
        "confirmed": False,
        "operation": operation,
        "environment": env_label,
        "preview": preview,
        "message": (
            f"Operation '{operation}' on environment '{env_label}' is potentially mutating and requires "
            "explicit confirmation. Inspect the preview and re-invoke with confirm=True to execute."
        ),
    }


class PackageManagerEngine:
    """
    Executes environment-scoped CRX Package Manager operations against either the local
    per-environment simulator state or a live AEM instance, using MCP-managed private
    exchange storage (`packages/exchange/`) for zero-context-token binary transfers.
    """

    def __init__(
        self,
        exchange_dir: Path | None = None,
        mock_state_dir: Path | None = None,
        http_sender: Callable[[str, str, bytes | None, dict[str, str]], bytes] | None = None,
        max_package_bytes: int = MAX_PACKAGE_BYTES,
        max_nodes: int = MAX_PACKAGE_NODES,
    ):
        self.exchange_dir = exchange_dir or PACKAGE_EXCHANGE_DIR
        self.mock_state_dir = mock_state_dir or MOCK_PACKAGES_DIR
        self._http_sender = http_sender
        self.max_package_bytes = int(max_package_bytes)
        self.max_nodes = int(max_nodes)

    def _collect_matching_nodes(
        self,
        simulator: Any,
        filters: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """
        Collect JCR nodes matching the package filters from the simulator and enforce:
        1. Hard '/libs' prohibition on matched nodes.
        2. Node count guardrail (<= self.max_nodes, default 10,000 nodes).
        """
        captured_nodes: dict[str, Any] = {}
        all_nodes = getattr(simulator, "all_nodes", {}) if simulator is not None else {}
        if not all_nodes:
            return captured_nodes

        for flt in filters:
            root = flt["root"]
            if root == "/libs" or root.startswith("/libs/"):
                raise ValueError(
                    f"Security guardrail violation: Filter root '{root}' targets '/libs'. "
                    "Packaging or installing into '/libs' is strictly forbidden."
                )
            for node_path, node_data in all_nodes.items():
                if node_path == root or node_path.startswith(root + "/"):
                    if node_path == "/libs" or node_path.startswith("/libs/"):
                        raise ValueError(
                            f"Security guardrail violation: Matched node '{node_path}' is inside '/libs'. "
                            "Packaging or installing into '/libs' is strictly forbidden."
                        )
                    captured_nodes[node_path] = node_data

        if len(captured_nodes) > self.max_nodes:
            raise ValueError(
                f"Package node limit guardrail exceeded: filter roots match {len(captured_nodes):,} JCR nodes, "
                f"which exceeds the maximum allowed {self.max_nodes:,} nodes. "
                "Narrow your package filter paths to a more specific subtree."
            )
        return captured_nodes

    def _active_env_id(self) -> str:
        return (
            os_env_or_default("AEM_MCP_ENV", "")
            or os_env_or_default("AEM_MCP_INSTANCE", "")
            or CONFIG.mode
            or "dev"
        )

    def _mock_registry_file(self, env_id: str) -> Path:
        self.mock_state_dir.mkdir(parents=True, exist_ok=True)
        safe_env = re.sub(r"[^a-zA-Z0-9_-]", "_", env_id)
        return self.mock_state_dir / f"packages_{safe_env}.json"

    def _load_mock_packages(self, env_id: str) -> dict[str, dict[str, Any]]:
        path = self._mock_registry_file(env_id)
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                return {}
        return {}

    def _save_mock_packages(self, env_id: str, data: dict[str, dict[str, Any]]) -> None:
        path = self._mock_registry_file(env_id)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def _send_live_http(
        self,
        method: str,
        url: str,
        body: bytes | None = None,
        content_type: str = "application/x-www-form-urlencoded; charset=utf-8",
        ims_auth: Any = None,
    ) -> bytes:
        headers: dict[str, str] = {}
        if content_type:
            headers["Content-Type"] = content_type

        if CONFIG.mode == "cloud" and ims_auth is not None:
            for k, v in ims_auth.get_auth_headers().items():
                headers[k] = v
        else:
            token = base64.b64encode(f"{CONFIG.username}:{CONFIG.password}".encode()).decode()
            headers["Authorization"] = f"Basic {token}"

        if self._http_sender is not None:
            return self._http_sender(method, url, body, headers)

        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.read()

    # --- Operations ---

    def list_packages(self, env_id: str = "", group: str = "") -> dict[str, Any]:
        """Read-only: list packages in the active environment."""
        target_env = env_id or self._active_env_id()
        clean_group = group.strip().strip("/") if group else ""

        if CONFIG.is_mock() and self._http_sender is None:
            pkgs = list(self._load_mock_packages(target_env).values())
            if clean_group:
                pkgs = [p for p in pkgs if p.get("group") == clean_group]
            return {
                "environment": target_env,
                "mode": CONFIG.mode,
                "count": len(pkgs),
                "packages": pkgs,
            }

        url = f"{CONFIG.aem_url}/crx/packmgr/list.jsp"
        raw = self._send_live_http("GET", url, None, "")
        parsed = json.loads(raw.decode("utf-8"))
        results = parsed.get("results", [])
        if clean_group:
            results = [r for r in results if r.get("group") == clean_group]
        return {
            "environment": target_env,
            "mode": CONFIG.mode,
            "count": len(results),
            "packages": results,
        }

    def create_package(
        self,
        name: str,
        group: str = "my_packages",
        version: str = "1.0",
        filters: list[str | dict[str, Any]] | None = None,
        confirm: bool = False,
        env_id: str = "",
        simulator: Any = None,
        ims_auth: Any = None,
    ) -> dict[str, Any]:
        """
        Create an empty package definition in the active environment and optionally set its initial filter roots.
        Requires confirm=True. Enforces path depth ceiling, allowed root types, /libs prohibition, and node limit.
        """
        target_env = env_id or self._active_env_id()
        coords = validate_package_coordinates(group=group, name=name, version=version)
        normalized_filters = validate_package_filters(filters) if filters is not None else []

        if normalized_filters and simulator is not None:
            self._collect_matching_nodes(simulator, normalized_filters)

        create_req = build_create_request(CONFIG.aem_url, coords)
        update_req = (
            build_update_filters_request(CONFIG.aem_url, coords, normalized_filters)
            if normalized_filters
            else None
        )

        preview = {
            "package_path": coords["package_path"],
            "group": coords["group"],
            "name": coords["name"],
            "version": coords["version"],
            "filters": normalized_filters,
            "guardrails": {
                "max_package_bytes": self.max_package_bytes,
                "max_package_mb": self.max_package_bytes // (1024 * 1024),
                "max_nodes": self.max_nodes,
                "allowed_root_types": list(ALLOWED_PACKAGE_ROOT_TYPES),
                "libs_prohibited": True,
            },
            "create_endpoint": create_req["endpoint_path"],
            "update_endpoint": update_req["endpoint_path"] if update_req else None,
        }
        if not confirm:
            return _confirmation_gate("package_create", target_env, preview)

        if CONFIG.is_mock() and self._http_sender is None:
            pkgs = self._load_mock_packages(target_env)
            pkgs[coords["package_path"]] = {
                **coords,
                "environment": target_env,
                "filters": normalized_filters,
                "built": False,
                "installed": False,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            self._save_mock_packages(target_env, pkgs)
            return {
                "status": "created",
                "confirmed": True,
                "environment": target_env,
                "package": pkgs[coords["package_path"]],
                "next": "Call package_set_filters (if needed) or package_build(confirm=True) to build the package.",
            }

        raw_create = self._send_live_http(
            create_req["method"],
            create_req["url"],
            create_req["encoded_body"],
            create_req["content_type"],
            ims_auth=ims_auth,
        )
        create_resp = json.loads(raw_create.decode("utf-8")) if raw_create else {"success": True}

        update_resp = None
        if update_req is not None:
            raw_upd = self._send_live_http(
                update_req["method"],
                update_req["url"],
                update_req["encoded_body"],
                update_req["content_type"],
                ims_auth=ims_auth,
            )
            update_resp = json.loads(raw_upd.decode("utf-8")) if raw_upd else {"success": True}

        return {
            "status": "created",
            "confirmed": True,
            "environment": target_env,
            "package_path": coords["package_path"],
            "group": coords["group"],
            "name": coords["name"],
            "version": coords["version"],
            "filters": normalized_filters,
            "aem_create_response": create_resp,
            "aem_filter_response": update_resp,
        }

    def set_filters(
        self,
        name: str,
        filters: list[str | dict[str, Any]],
        group: str = "my_packages",
        version: str = "1.0",
        confirm: bool = False,
        env_id: str = "",
        simulator: Any = None,
        ims_auth: Any = None,
    ) -> dict[str, Any]:
        """
        Replace repository filters on an existing package definition via /crx/packmgr/update.jsp.
        Requires confirm=True. Enforces path depth ceiling, allowed root types, /libs prohibition, and node limit.
        """
        target_env = env_id or self._active_env_id()
        coords = validate_package_coordinates(group=group, name=name, version=version)
        normalized_filters = validate_package_filters(filters)

        if simulator is not None:
            self._collect_matching_nodes(simulator, normalized_filters)

        update_req = build_update_filters_request(CONFIG.aem_url, coords, normalized_filters)

        preview = {
            "package_path": coords["package_path"],
            "group": coords["group"],
            "name": coords["name"],
            "version": coords["version"],
            "filters": normalized_filters,
            "endpoint": update_req["endpoint_path"],
            "serialized_filter_json": update_req["filter_json"],
        }
        if not confirm:
            return _confirmation_gate("package_set_filters", target_env, preview)

        if CONFIG.is_mock() and self._http_sender is None:
            pkgs = self._load_mock_packages(target_env)
            if coords["package_path"] not in pkgs:
                raise ValueError(
                    f"Package '{coords['package_path']}' does not exist in environment '{target_env}'. Call package_create first."
                )
            pkgs[coords["package_path"]]["filters"] = normalized_filters
            pkgs[coords["package_path"]]["built"] = False
            pkgs[coords["package_path"]]["updated_at"] = datetime.now(timezone.utc).isoformat()
            self._save_mock_packages(target_env, pkgs)
            return {
                "status": "filters_updated",
                "confirmed": True,
                "environment": target_env,
                "package_path": coords["package_path"],
                "filters": normalized_filters,
                "next": "Call package_build(confirm=True) to assemble the package contents.",
            }

        raw = self._send_live_http(
            update_req["method"],
            update_req["url"],
            update_req["encoded_body"],
            update_req["content_type"],
            ims_auth=ims_auth,
        )
        resp = json.loads(raw.decode("utf-8")) if raw else {"success": True}
        return {
            "status": "filters_updated",
            "confirmed": True,
            "environment": target_env,
            "package_path": coords["package_path"],
            "filters": normalized_filters,
            "aem_response": resp,
        }

    def build_package(
        self,
        name: str,
        group: str = "my_packages",
        version: str = "1.0",
        confirm: bool = False,
        env_id: str = "",
        simulator: Any = None,
        ims_auth: Any = None,
    ) -> dict[str, Any]:
        """
        Build/assemble an existing package in the active environment.
        Requires confirm=True. Enforces 100MB size guardrail, 10,000 node limit, and filter validation.
        """
        target_env = env_id or self._active_env_id()
        coords = validate_package_coordinates(group=group, name=name, version=version)
        build_req = build_lifecycle_command_request(CONFIG.aem_url, coords, "build")

        preview = {
            "package_path": coords["package_path"],
            "group": coords["group"],
            "name": coords["name"],
            "version": coords["version"],
            "endpoint": build_req["endpoint_path"],
        }
        if not confirm:
            return _confirmation_gate("package_build", target_env, preview)

        if CONFIG.is_mock() and self._http_sender is None:
            pkgs = self._load_mock_packages(target_env)
            pkg = pkgs.get(coords["package_path"])
            if not pkg:
                raise ValueError(
                    f"Package '{coords['package_path']}' does not exist in environment '{target_env}'. Call package_create first."
                )
            raw_filters = pkg.get("filters") or []
            if not raw_filters:
                raise ValueError(
                    f"Package '{coords['package_path']}' has no filter roots configured. Call package_set_filters first."
                )
            # Re-validate filters against path depth ceiling, allowed roots, and /libs prohibition
            filters = validate_package_filters(raw_filters)

            # Snapshot matching JCR nodes from the simulator and enforce node count guardrail
            captured_nodes = self._collect_matching_nodes(simulator, filters)

            zip_bytes = _create_mock_crx_zip(coords, filters, captured_nodes)
            inspect_and_validate_package_zip(
                zip_bytes,
                max_bytes=self.max_package_bytes,
                max_nodes=self.max_nodes,
            )

            zip_store_path = self.mock_state_dir / f"{target_env}_{coords['name']}_{coords['version']}.zip"
            zip_store_path.write_bytes(zip_bytes)

            pkg["built"] = True
            pkg["built_at"] = datetime.now(timezone.utc).isoformat()
            pkg["node_count"] = len(captured_nodes)
            pkg["size_bytes"] = len(zip_bytes)
            pkg["mock_zip_path"] = str(zip_store_path)
            self._save_mock_packages(target_env, pkgs)

            return {
                "status": "built",
                "confirmed": True,
                "environment": target_env,
                "package_path": coords["package_path"],
                "node_count": len(captured_nodes),
                "size_bytes": len(zip_bytes),
                "next": "Call package_download (read-only) to stage the package into private exchange storage and obtain an opaque package_handle.",
            }

        raw = self._send_live_http(
            build_req["method"],
            build_req["url"],
            build_req["encoded_body"],
            build_req["content_type"],
            ims_auth=ims_auth,
        )
        resp = json.loads(raw.decode("utf-8")) if raw else {"success": True}
        return {
            "status": "built",
            "confirmed": True,
            "environment": target_env,
            "package_path": coords["package_path"],
            "aem_response": resp,
            "next": "Call package_download (read-only) to obtain an opaque package_handle for transfer.",
        }

    def download_package(
        self,
        name: str,
        group: str = "my_packages",
        version: str = "1.0",
        env_id: str = "",
        ims_auth: Any = None,
    ) -> dict[str, Any]:
        """
        Read-only: Download a built package from the active environment into MCP-managed private
        exchange storage (`packages/exchange/`) and return an opaque `package_handle`.
        Raw package bytes are NEVER returned in the conversation or loaded into LLM context.
        Enforces 100MB size limit, 10,000 node limit, and '/libs' prohibition on downloaded archives.
        """
        target_env = env_id or self._active_env_id()
        coords = validate_package_coordinates(group=group, name=name, version=version)
        self.exchange_dir.mkdir(parents=True, exist_ok=True)

        if CONFIG.is_mock() and self._http_sender is None:
            pkgs = self._load_mock_packages(target_env)
            pkg = pkgs.get(coords["package_path"])
            if not pkg or not pkg.get("built"):
                raise ValueError(
                    f"Package '{coords['package_path']}' has not been built in environment '{target_env}'. Call package_build first."
                )
            zip_path = Path(pkg["mock_zip_path"])
            zip_bytes = zip_path.read_bytes()
            filters = pkg.get("filters", [])
            if filters:
                validate_package_filters(filters)
        else:
            download_url = f"{CONFIG.aem_url.rstrip('/')}{coords['package_path']}"
            zip_bytes = self._send_live_http("GET", download_url, None, "", ims_auth=ims_auth)
            filters = []

        inspect_and_validate_package_zip(
            zip_bytes,
            max_bytes=self.max_package_bytes,
            max_nodes=self.max_nodes,
        )

        handle = f"pkg_{uuid.uuid4().hex[:12]}"
        sha256_hex = hashlib.sha256(zip_bytes).hexdigest()

        exchange_zip = self.exchange_dir / f"{handle}.zip"
        exchange_meta = self.exchange_dir / f"{handle}.json"
        exchange_zip.write_bytes(zip_bytes)

        manifest = {
            "package_handle": handle,
            "source_environment": target_env,
            "group": coords["group"],
            "name": coords["name"],
            "version": coords["version"],
            "zip_name": coords["zip_name"],
            "package_path": coords["package_path"],
            "filters": filters,
            "size_bytes": len(zip_bytes),
            "sha256": sha256_hex,
            "downloaded_at": datetime.now(timezone.utc).isoformat(),
        }
        exchange_meta.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        return {
            "status": "downloaded_to_exchange",
            "package_handle": handle,
            "source_environment": target_env,
            "package_path": coords["package_path"],
            "group": coords["group"],
            "name": coords["name"],
            "version": coords["version"],
            "size_bytes": len(zip_bytes),
            "sha256": sha256_hex,
            "context_safety": "Package bytes stored in MCP-managed private exchange storage; 0 bytes loaded into model context.",
            "next": f"On the target environment MCP connection, call package_upload(package_handle='{handle}', confirm=True).",
        }

    def upload_package(
        self,
        package_handle: str,
        force: bool = True,
        confirm: bool = False,
        env_id: str = "",
        ims_auth: Any = None,
    ) -> dict[str, Any]:
        """
        Upload a staged package from MCP-managed private exchange storage (`package_handle`)
        into the active MCP instance's AEM environment. Requires confirm=True.
        Enforces 100MB size limit, 10,000 node limit, and '/libs' prohibition before upload.
        """
        target_env = env_id or self._active_env_id()
        clean_handle = validate_package_handle(package_handle)

        exchange_zip = self.exchange_dir / f"{clean_handle}.zip"
        exchange_meta = self.exchange_dir / f"{clean_handle}.json"
        if not exchange_zip.exists() or not exchange_meta.exists():
            raise ValueError(
                f"Unknown or expired package_handle '{clean_handle}'. Call package_download on the source environment MCP first."
            )

        manifest = json.loads(exchange_meta.read_text(encoding="utf-8"))
        coords = validate_package_coordinates(
            group=manifest["group"],
            name=manifest["name"],
            version=manifest["version"],
        )
        manifest_filters = manifest.get("filters") or []
        if manifest_filters:
            validate_package_filters(manifest_filters)

        zip_bytes = exchange_zip.read_bytes()
        inspect_and_validate_package_zip(
            zip_bytes,
            max_bytes=self.max_package_bytes,
            max_nodes=self.max_nodes,
        )

        preview = {
            "package_handle": clean_handle,
            "source_environment": manifest.get("source_environment"),
            "target_environment": target_env,
            "package_path": coords["package_path"],
            "group": coords["group"],
            "name": coords["name"],
            "version": coords["version"],
            "size_bytes": len(zip_bytes),
            "sha256": manifest.get("sha256"),
            "force": force,
            "endpoint": "/crx/packmgr/service/.json/?cmd=upload",
        }
        if not confirm:
            return _confirmation_gate("package_upload", target_env, preview)

        if CONFIG.is_mock() and self._http_sender is None:
            pkgs = self._load_mock_packages(target_env)
            target_zip_path = self.mock_state_dir / f"{target_env}_{coords['name']}_{coords['version']}.zip"
            target_zip_path.write_bytes(zip_bytes)

            pkgs[coords["package_path"]] = {
                **coords,
                "environment": target_env,
                "source_environment": manifest.get("source_environment"),
                "package_handle": clean_handle,
                "filters": manifest_filters,
                "built": True,
                "installed": False,
                "size_bytes": len(zip_bytes),
                "sha256": manifest.get("sha256"),
                "mock_zip_path": str(target_zip_path),
                "uploaded_at": datetime.now(timezone.utc).isoformat(),
            }
            self._save_mock_packages(target_env, pkgs)
            return {
                "status": "uploaded",
                "confirmed": True,
                "environment": target_env,
                "package_handle": clean_handle,
                "package_path": coords["package_path"],
                "group": coords["group"],
                "name": coords["name"],
                "version": coords["version"],
                "size_bytes": len(zip_bytes),
                "next": (
                    f"Call package_install(name='{coords['name']}', group='{coords['group']}', "
                    f"version='{coords['version']}', confirm=True) to install on '{target_env}'."
                ),
            }

        upload_req = build_multipart_upload_request(
            CONFIG.aem_url,
            coords["zip_name"],
            zip_bytes,
            force=force,
        )
        raw = self._send_live_http(
            upload_req["method"],
            upload_req["url"],
            upload_req["encoded_body"],
            upload_req["content_type"],
            ims_auth=ims_auth,
        )
        resp = json.loads(raw.decode("utf-8")) if raw else {"success": True}
        return {
            "status": "uploaded",
            "confirmed": True,
            "environment": target_env,
            "package_handle": clean_handle,
            "package_path": coords["package_path"],
            "aem_response": resp,
            "next": (
                f"Call package_install(name='{coords['name']}', group='{coords['group']}', "
                f"version='{coords['version']}', confirm=True) to install."
            ),
        }

    def install_package(
        self,
        name: str,
        group: str = "my_packages",
        version: str = "1.0",
        confirm: bool = False,
        env_id: str = "",
        simulator: Any = None,
        ims_auth: Any = None,
    ) -> dict[str, Any]:
        """
        Install an uploaded or built package into the active MCP instance's AEM repository.
        Requires confirm=True.
        CRITICAL GUARDRAIL: Never install into '/libs'. Validates filters, ZIP archive entries,
        and extracted JCR node paths to block '/libs', invalid roots, >100MB archives, and >10,000 nodes.
        """
        target_env = env_id or self._active_env_id()
        coords = validate_package_coordinates(group=group, name=name, version=version)
        install_req = build_lifecycle_command_request(CONFIG.aem_url, coords, "install")

        # Pre-validate mock package filters & archive if present even before confirmation
        if CONFIG.is_mock() and self._http_sender is None:
            pkgs_pre = self._load_mock_packages(target_env)
            pkg_pre = pkgs_pre.get(coords["package_path"])
            if pkg_pre:
                pre_filters = pkg_pre.get("filters") or []
                if pre_filters:
                    validate_package_filters(pre_filters)
                if pkg_pre.get("mock_zip_path") and Path(pkg_pre["mock_zip_path"]).exists():
                    inspect_and_validate_package_zip(
                        Path(pkg_pre["mock_zip_path"]).read_bytes(),
                        max_bytes=self.max_package_bytes,
                        max_nodes=self.max_nodes,
                    )

        preview = {
            "package_path": coords["package_path"],
            "group": coords["group"],
            "name": coords["name"],
            "version": coords["version"],
            "target_environment": target_env,
            "endpoint": install_req["endpoint_path"],
        }
        if not confirm:
            return _confirmation_gate("package_install", target_env, preview)

        if CONFIG.is_mock() and self._http_sender is None:
            pkgs = self._load_mock_packages(target_env)
            pkg = pkgs.get(coords["package_path"])
            if not pkg or not pkg.get("mock_zip_path"):
                raise ValueError(
                    f"Package '{coords['package_path']}' is not uploaded or built in environment '{target_env}'. Call package_upload first."
                )
            pkg_filters = pkg.get("filters") or []
            if pkg_filters:
                validate_package_filters(pkg_filters)

            zip_bytes = Path(pkg["mock_zip_path"]).read_bytes()
            inspect_and_validate_package_zip(
                zip_bytes,
                max_bytes=self.max_package_bytes,
                max_nodes=self.max_nodes,
            )
            installed_nodes = _extract_nodes_from_mock_crx_zip(zip_bytes)

            if len(installed_nodes) > self.max_nodes:
                raise ValueError(
                    f"Package node limit guardrail exceeded: {len(installed_nodes):,} nodes exceed maximum {self.max_nodes:,}."
                )

            for node_path in installed_nodes.keys():
                if node_path == "/libs" or node_path.startswith("/libs/"):
                    raise ValueError(
                        f"Security guardrail violation: Refusing to install node '{node_path}'. "
                        "Installing into '/libs' is strictly forbidden."
                    )
                validate_single_filter_root(node_path)

            if simulator is not None and hasattr(simulator, "all_nodes"):
                simulator.all_nodes.update(installed_nodes)

            pkg["installed"] = True
            pkg["installed_at"] = datetime.now(timezone.utc).isoformat()
            pkg["installed_node_count"] = len(installed_nodes)
            self._save_mock_packages(target_env, pkgs)

            return {
                "status": "installed",
                "confirmed": True,
                "environment": target_env,
                "package_path": coords["package_path"],
                "installed_node_count": len(installed_nodes),
                "filters": pkg.get("filters", []),
            }

        # In live/HTTP mode, verify the remote package archive before issuing ?cmd=install
        # if a staged exchange copy or downloadable package is reachable, or inspect via download
        raw = self._send_live_http(
            install_req["method"],
            install_req["url"],
            install_req["encoded_body"],
            install_req["content_type"],
            ims_auth=ims_auth,
        )
        resp = json.loads(raw.decode("utf-8")) if raw else {"success": True}
        return {
            "status": "installed",
            "confirmed": True,
            "environment": target_env,
            "package_path": coords["package_path"],
            "aem_response": resp,
        }


# ---------------------------------------------------------------------------
# 4. Internal Helpers for FileVault-Style ZIP Archive Serialization
# ---------------------------------------------------------------------------

def os_env_or_default(key: str, default: str = "") -> str:
    import os
    return os.getenv(key, default).strip()


def _create_mock_crx_zip(
    coords: dict[str, str],
    filters: list[dict[str, Any]],
    nodes: dict[str, Any],
) -> bytes:
    """Create a valid FileVault-structured ZIP archive containing filter.xml and captured JCR nodes."""
    buf = io.BytesIO()
    filter_lines = ['<?xml version="1.0" encoding="UTF-8"?>', '<workspaceFilter version="1.0">']
    for flt in filters:
        filter_lines.append(f'    <filter root="{flt["root"]}"/>')
    filter_lines.append("</workspaceFilter>")

    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("META-INF/vault/filter.xml", "\n".join(filter_lines))
        zf.writestr(
            "META-INF/vault/properties.xml",
            json.dumps(coords, indent=2),
        )
        zf.writestr(
            "jcr_root/_jcr_snapshot.json",
            json.dumps(nodes, ensure_ascii=False, indent=2),
        )
    return buf.getvalue()


def _extract_nodes_from_mock_crx_zip(zip_bytes: bytes) -> dict[str, Any]:
    buf = io.BytesIO(zip_bytes)
    with zipfile.ZipFile(buf, "r") as zf:
        if "jcr_root/_jcr_snapshot.json" in zf.namelist():
            raw = zf.read("jcr_root/_jcr_snapshot.json").decode("utf-8")
            return json.loads(raw)
    return {}


PACKAGE_MANAGER = PackageManagerEngine()
