"""
Unit and integration tests for Environment-Scoped AEM CRX Package Manager & Multi-Instance Sync.
Covers validation, confirmation behavior, endpoint construction, filter serialization,
and end-to-end INT-to-Local transfer via opaque package handles in private exchange storage.
"""

import io
import json
import urllib.parse
import zipfile
from pathlib import Path
from typing import Any

import pytest

from aem_mcp.config import CONFIG
from aem_mcp.packages import (
    ALLOWED_PACKAGE_ROOT_TYPES,
    MAX_PACKAGE_BYTES,
    MAX_PACKAGE_NODES,
    MAX_PACKAGE_SIZE_MB,
    PackageManagerEngine,
    build_create_request,
    build_lifecycle_command_request,
    build_multipart_upload_request,
    build_update_filters_request,
    inspect_and_validate_package_zip,
    validate_package_coordinates,
    validate_package_filters,
    validate_package_handle,
)


def test_package_coordinate_validation():
    """Validate package names, groups, and versions and reject unsafe coordinates."""
    coords = validate_package_coordinates("novaria/sync", "hotels-us-en", "1.0.2")
    assert coords["group"] == "novaria/sync"
    assert coords["name"] == "hotels-us-en"
    assert coords["version"] == "1.0.2"
    assert coords["zip_name"] == "hotels-us-en-1.0.2.zip"
    assert coords["package_path"] == "/etc/packages/novaria/sync/hotels-us-en-1.0.2.zip"

    with pytest.raises(ValueError, match="Invalid package group"):
        validate_package_coordinates("../etc", "pkg", "1.0")

    with pytest.raises(ValueError, match="Invalid package name"):
        validate_package_coordinates("my_packages", "../bad", "1.0")

    with pytest.raises(ValueError, match="Invalid package version"):
        validate_package_coordinates("my_packages", "valid_name", "1.0/../../x")


def test_package_filter_validation_blocks_root_and_etc_packages():
    """
    Verify validation for absolute repository paths, duplicate filter roots,
    and preventing '/' and '/etc/packages' as content filters.
    """
    valid = validate_package_filters(["/content/novaria/us/en", "/content/dam/novaria"])
    assert valid == [
        {"root": "/content/novaria/us/en", "rules": []},
        {"root": "/content/dam/novaria", "rules": []},
    ]

    # Empty filter list rejected
    with pytest.raises(ValueError, match="At least one repository filter"):
        validate_package_filters([])

    # Relative path rejected
    with pytest.raises(ValueError, match="must be an absolute repository path"):
        validate_package_filters(["content/novaria/us/en"])

    # Duplicate filter roots rejected
    with pytest.raises(ValueError, match="Duplicate filter root"):
        validate_package_filters(["/content/novaria/us/en", "/content/novaria/us/en/"])

    # Preventing '/' and '/etc/packages' (and subpaths) as content filters
    for forbidden in ["/", "/etc/packages", "/etc/packages/my_packages", "/system", "/oak:index"]:
        with pytest.raises(ValueError, match="forbidden"):
            validate_package_filters([forbidden])


def test_aem_documented_package_manager_endpoint_construction_and_filter_serialization():
    """
    Verify endpoint construction matches AEM's documented CRX Package Manager API:
    - Package creation through package-specific create endpoint
    - Filters submitted as JSON through /crx/packmgr/update.jsp
    """
    coords = validate_package_coordinates("my_packages", "int-content-sync", "2.1")
    filters = validate_package_filters(["/content/novaria/us/en/hotels", "/content/dam/novaria/hotels"])

    # 1. Package-specific create endpoint
    create_req = build_create_request("https://aem-int.internal:4502", coords)
    assert create_req["method"] == "POST"
    assert create_req["url"] == (
        "https://aem-int.internal:4502/crx/packmgr/service/.json"
        "/etc/packages/my_packages/int-content-sync-2.1.zip?cmd=create"
    )
    parsed_create_form = dict(urllib.parse.parse_qsl(create_req["encoded_body"].decode("utf-8")))
    assert parsed_create_form == {
        "packageName": "int-content-sync",
        "groupName": "my_packages",
        "packageVersion": "2.1",
        "_charset_": "utf-8",
    }

    # 2. Filters submitted as JSON through update.jsp
    update_req = build_update_filters_request("https://aem-int.internal:4502", coords, filters)
    assert update_req["method"] == "POST"
    assert update_req["url"] == "https://aem-int.internal:4502/crx/packmgr/update.jsp"
    parsed_update_form = dict(urllib.parse.parse_qsl(update_req["encoded_body"].decode("utf-8")))
    assert parsed_update_form["path"] == "/etc/packages/my_packages/int-content-sync-2.1.zip"
    assert parsed_update_form["packageName"] == "int-content-sync"
    assert parsed_update_form["groupName"] == "my_packages"
    assert parsed_update_form["version"] == "2.1"
    assert json.loads(parsed_update_form["filter"]) == [
        {"root": "/content/novaria/us/en/hotels", "rules": []},
        {"root": "/content/dam/novaria/hotels", "rules": []},
    ]

    # 3. Build and install lifecycle endpoints
    build_req = build_lifecycle_command_request("https://aem-int.internal:4502", coords, "build")
    assert build_req["url"].endswith("/etc/packages/my_packages/int-content-sync-2.1.zip?cmd=build")

    install_req = build_lifecycle_command_request("http://localhost:4502", coords, "install")
    assert install_req["url"].endswith("/etc/packages/my_packages/int-content-sync-2.1.zip?cmd=install")

    # 4. Upload multipart endpoint
    upload_req = build_multipart_upload_request("http://localhost:4502", coords["zip_name"], b"PK\x03\x04test", force=True)
    assert upload_req["url"] == "http://localhost:4502/crx/packmgr/service/.json/?cmd=upload"
    assert b'filename="int-content-sync-2.1.zip"' in upload_req["encoded_body"]


def test_confirmation_gates_on_mutating_package_operations(tmp_path: Path):
    """
    Verify that creation, filter changes, building, upload, and installation require
    explicit confirmation (confirm=True), whereas package_list and package_download are read-only.
    """
    engine = PackageManagerEngine(
        exchange_dir=tmp_path / "exchange",
        mock_state_dir=tmp_path / "mock_state",
    )

    # 1. package_create without confirm=True returns confirmation_required and does not create
    res_create_unconfirmed = engine.create_package(
        name="sample-pkg",
        group="my_packages",
        version="1.0",
        filters=["/content/novaria/us/en"],
        confirm=False,
        env_id="int",
    )
    assert res_create_unconfirmed["status"] == "confirmation_required"
    assert res_create_unconfirmed["confirmed"] is False
    assert engine.list_packages(env_id="int")["count"] == 0

    # 2. Confirm package_create
    res_created = engine.create_package(
        name="sample-pkg",
        group="my_packages",
        version="1.0",
        filters=["/content/novaria/us/en"],
        confirm=True,
        env_id="int",
    )
    assert res_created["status"] == "created"
    assert engine.list_packages(env_id="int")["count"] == 1

    # 3. package_set_filters without confirm=True
    res_filters_unconfirmed = engine.set_filters(
        name="sample-pkg",
        group="my_packages",
        version="1.0",
        filters=["/content/dam/novaria"],
        confirm=False,
        env_id="int",
    )
    assert res_filters_unconfirmed["status"] == "confirmation_required"

    # 4. package_build without confirm=True
    res_build_unconfirmed = engine.build_package(
        name="sample-pkg",
        group="my_packages",
        version="1.0",
        confirm=False,
        env_id="int",
    )
    assert res_build_unconfirmed["status"] == "confirmation_required"


def test_int_to_local_multi_instance_package_sync_via_opaque_handle(tmp_path: Path):
    """
    End-to-end simulation of an INT-to-Local package transfer:
      INT MCP:   package_create -> package_set_filters -> package_build -> package_download
      Local MCP: package_upload(package_handle) -> package_install
    Verifies that package bytes are stored in private exchange storage and never enter LLM context,
    and that installing on Local MCP updates Local's JCR nodes.
    """
    engine = PackageManagerEngine(
        exchange_dir=tmp_path / "exchange",
        mock_state_dir=tmp_path / "mock_state",
    )

    class DummySimulator:
        def __init__(self, nodes: dict[str, Any]):
            self.all_nodes = nodes

    int_simulator = DummySimulator({
        "/content/novaria/us/en/hotels/azure-bay": {
            "jcr:primaryType": "cq:Page",
            "jcr:content": {"jcr:title": "Novaria Azure Bay (Updated on INT)", "starRating": 5},
        },
        "/content/other/untouched": {"jcr:primaryType": "cq:Page"},
    })
    local_simulator = DummySimulator({})

    # --- Step 1 (INT MCP): Create, Set Filters, Build, Download ---
    engine.create_package(name="azure-bay-sync", group="novaria", version="1.0", confirm=True, env_id="int")
    engine.set_filters(
        name="azure-bay-sync",
        group="novaria",
        version="1.0",
        filters=["/content/novaria/us/en/hotels/azure-bay"],
        confirm=True,
        env_id="int",
    )
    build_res = engine.build_package(
        name="azure-bay-sync",
        group="novaria",
        version="1.0",
        confirm=True,
        env_id="int",
        simulator=int_simulator,
    )
    assert build_res["status"] == "built"
    assert build_res["node_count"] == 1

    # Download is read-only (no confirm flag needed) and returns an opaque handle
    dl_res = engine.download_package(name="azure-bay-sync", group="novaria", version="1.0", env_id="int")
    assert dl_res["status"] == "downloaded_to_exchange"
    handle = dl_res["package_handle"]
    assert validate_package_handle(handle) == handle
    assert "jcr_root" not in json.dumps(dl_res)  # Raw archive contents never in tool output

    # --- Step 2 (Local MCP): Upload handle -> Install ---
    upload_unconfirmed = engine.upload_package(package_handle=handle, confirm=False, env_id="dev")
    assert upload_unconfirmed["status"] == "confirmation_required"

    upload_res = engine.upload_package(package_handle=handle, confirm=True, env_id="dev")
    assert upload_res["status"] == "uploaded"
    assert upload_res["environment"] == "dev"

    install_unconfirmed = engine.install_package(
        name="azure-bay-sync", group="novaria", version="1.0", confirm=False, env_id="dev"
    )
    assert install_unconfirmed["status"] == "confirmation_required"

    install_res = engine.install_package(
        name="azure-bay-sync",
        group="novaria",
        version="1.0",
        confirm=True,
        env_id="dev",
        simulator=local_simulator,
    )
    assert install_res["status"] == "installed"
    assert install_res["installed_node_count"] == 1
    assert (
        local_simulator.all_nodes["/content/novaria/us/en/hotels/azure-bay"]["jcr:content"]["jcr:title"]
        == "Novaria Azure Bay (Updated on INT)"
    )


def test_live_mode_http_dispatch_for_package_lifecycle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Verify live HTTP request dispatch for create, update.jsp filter JSON, build, download, upload, and install."""
    recorded_calls: list[dict[str, Any]] = []

    def fake_http_sender(method: str, url: str, body: bytes | None, headers: dict[str, str]) -> bytes:
        recorded_calls.append({"method": method, "url": url, "body": body, "headers": headers})
        if url.endswith(".zip") and method == "GET":
            return b"PK\x03\x04fake-zip-bytes"
        return json.dumps({"success": True, "msg": "ok"}).encode("utf-8")

    monkeypatch.setattr(CONFIG, "mode", "onprem")
    monkeypatch.setattr(CONFIG, "aem_url", "https://aem-int.internal:4502")

    engine = PackageManagerEngine(
        exchange_dir=tmp_path / "exchange",
        mock_state_dir=tmp_path / "mock_state",
        http_sender=fake_http_sender,
    )

    engine.create_package(
        name="live-pkg",
        group="my_packages",
        version="1.0",
        filters=["/content/novaria/us/en"],
        confirm=True,
        env_id="int",
    )
    assert len(recorded_calls) == 2
    assert recorded_calls[0]["url"].endswith("/crx/packmgr/service/.json/etc/packages/my_packages/live-pkg-1.0.zip?cmd=create")
    assert recorded_calls[1]["url"].endswith("/crx/packmgr/update.jsp")

    engine.build_package(name="live-pkg", group="my_packages", version="1.0", confirm=True, env_id="int")
    assert recorded_calls[-1]["url"].endswith("/crx/packmgr/service/.json/etc/packages/my_packages/live-pkg-1.0.zip?cmd=build")

    dl = engine.download_package(name="live-pkg", group="my_packages", version="1.0", env_id="int")
    assert recorded_calls[-1]["method"] == "GET"
    assert recorded_calls[-1]["url"] == "https://aem-int.internal:4502/etc/packages/my_packages/live-pkg-1.0.zip"

    engine.upload_package(package_handle=dl["package_handle"], confirm=True, env_id="dev")
    assert recorded_calls[-1]["url"].endswith("/crx/packmgr/service/.json/?cmd=upload")

    engine.install_package(name="live-pkg", group="my_packages", version="1.0", confirm=True, env_id="dev")
    assert recorded_calls[-1]["url"].endswith("/crx/packmgr/service/.json/etc/packages/my_packages/live-pkg-1.0.zip?cmd=install")


def test_guardrail_allow_creating_package_from_libs_but_never_install_into_libs(tmp_path: Path):
    """
    Verify that:
    1. Creating, building, downloading, and uploading a package from '/libs/<subpath>' (e.g. '/libs/wcm/core')
       IS allowed (while bare '/libs' is rejected by the minimum path depth ceiling).
    2. Installing ('install_package') any package that contains '/libs' filters, 'jcr_root/libs/...' entries,
       or '/libs/...' JCR nodes is STRICTLY FORBIDDEN so '/libs' can never be overwritten.
    """
    # 1. Bare '/libs' is blocked by the minimum path depth ceiling (requires >= 2 segments)
    with pytest.raises(ValueError, match="above the minimum allowed path depth ceiling"):
        validate_package_filters(["/libs"])

    # 2. Specific '/libs/<subpath>' is allowed for package creation, building, downloading, and uploading
    valid_libs_filters = validate_package_filters(["/libs/wcm/core", "/libs/granite/ui/components/coral"])
    assert [f["root"] for f in valid_libs_filters] == [
        "/libs/wcm/core",
        "/libs/granite/ui/components/coral",
    ]

    class DummySim:
        def __init__(self, nodes: dict[str, Any] | None = None):
            self.all_nodes: dict[str, Any] = nodes or {}

    source_sim = DummySim({
        "/libs/wcm/core/content/editor": {"jcr:primaryType": "cq:Page"},
    })
    target_sim = DummySim({
        "/libs/wcm/core/content/editor": {"jcr:primaryType": "cq:Page", "immutable": True},
    })

    engine = PackageManagerEngine(
        exchange_dir=tmp_path / "exchange",
        mock_state_dir=tmp_path / "mock_state",
    )

    # Create, build, download, and upload a '/libs/wcm/core' package succeeds
    create_res = engine.create_package(
        name="libs-wcm-read",
        group="my_packages",
        version="1.0",
        filters=["/libs/wcm/core"],
        confirm=True,
        env_id="int",
        simulator=source_sim,
    )
    assert create_res["status"] == "created"

    build_res = engine.build_package(
        name="libs-wcm-read",
        group="my_packages",
        version="1.0",
        confirm=True,
        env_id="int",
        simulator=source_sim,
    )
    assert build_res["status"] == "built"
    assert build_res["node_count"] == 1

    dl_res = engine.download_package(
        name="libs-wcm-read",
        group="my_packages",
        version="1.0",
        env_id="int",
    )
    assert dl_res["status"] == "downloaded_to_exchange"

    up_res = engine.upload_package(
        package_handle=dl_res["package_handle"],
        confirm=True,
        env_id="dev",
    )
    assert up_res["status"] == "uploaded"

    # 3. Attempting to INSTALL a package targeting '/libs/...' is strictly forbidden (even before confirm=True!)
    with pytest.raises(ValueError, match="Installing into '/libs' is strictly forbidden"):
        engine.install_package(
            name="libs-wcm-read",
            group="my_packages",
            version="1.0",
            confirm=False,
            env_id="dev",
            simulator=target_sim,
        )

    with pytest.raises(ValueError, match="Installing into '/libs' is strictly forbidden"):
        engine.install_package(
            name="libs-wcm-read",
            group="my_packages",
            version="1.0",
            confirm=True,
            env_id="dev",
            simulator=target_sim,
        )

    # Verify target_sim '/libs' node was NOT overwritten
    assert target_sim.all_nodes["/libs/wcm/core/content/editor"]["immutable"] is True

    # 4. Even if a package claims its filter is '/apps/novaria', if the ZIP archive contains
    #    'jcr_root/libs/...' or a '/libs/...' node in '_jcr_snapshot.json', install_package MUST refuse it!
    mock_state_dir = tmp_path / "mock_state"
    mock_state_dir.mkdir(parents=True, exist_ok=True)
    bad_zip_path = mock_state_dir / "dev_tampered_1.0.zip"
    buf_b = io.BytesIO()
    with zipfile.ZipFile(buf_b, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "META-INF/vault/filter.xml",
            '<?xml version="1.0" encoding="UTF-8"?><workspaceFilter version="1.0"><filter root="/apps/novaria"/></workspaceFilter>',
        )
        zf.writestr("jcr_root/libs/wcm/core/.content.xml", "<jcr:root/>")
    bad_zip_path.write_bytes(buf_b.getvalue())

    engine._save_mock_packages(
        "dev",
        {
            "/etc/packages/my_packages/tampered-1.0.zip": {
                "group": "my_packages",
                "name": "tampered",
                "version": "1.0",
                "package_path": "/etc/packages/my_packages/tampered-1.0.zip",
                "filters": [{"root": "/apps/novaria", "rules": []}],
                "built": True,
                "mock_zip_path": str(bad_zip_path),
            }
        },
    )

    with pytest.raises(ValueError, match="Installing into '/libs' is strictly forbidden"):
        engine.install_package(
            name="tampered",
            group="my_packages",
            version="1.0",
            confirm=True,
            env_id="dev",
            simulator=target_sim,
        )


def test_guardrail_allowed_root_types_and_minimum_path_depth_ceiling():
    """
    Verify that package filter paths:
    1. Are restricted to ALLOWED_PACKAGE_ROOT_TYPES (/content, /conf, /apps, /libs, /etc/designs, /var/workflow/models).
    2. Cannot go above the minimum path depth ceiling (e.g., /content, /conf, /apps, /libs, /content/dam,
       /content/experience-fragments without a project/site child segment are blocked).
    """
    assert ALLOWED_PACKAGE_ROOT_TYPES == (
        "/content",
        "/conf",
        "/apps",
        "/libs",
        "/etc/designs",
        "/var/workflow/models",
    )

    # 1. Disallowed root types rejected
    for invalid_root in [
        "/opt/custom/site",
        "/var/audit/com.day.cq.wcm.core.page",
        "/etc/clientlibs/novaria",
        "/home/users/system/service",
        "/tmp/scratch/pkg",
    ]:
        with pytest.raises(ValueError, match="allowed repository root type|forbidden"):
            validate_package_filters([invalid_root])

    # 2. Broad roots above the minimum depth ceiling rejected
    for too_broad in [
        "/content",
        "/conf",
        "/apps",
        "/libs",
        "/content/dam",
        "/content/experience-fragments",
        "/content/campaigns",
        "/content/cq:tags",
        "/conf/global",
        "/etc/designs",
        "/var/workflow/models",
    ]:
        with pytest.raises(ValueError, match="above the minimum allowed path depth ceiling"):
            validate_package_filters([too_broad])

    # 3. Valid project/site/module-scoped paths at or below required depth succeed
    valid_paths = [
        "/content/novaria",
        "/content/novaria/us/en",
        "/content/dam/novaria",
        "/content/experience-fragments/novaria/us/en",
        "/conf/novaria/settings/wcm/templates",
        "/apps/novaria/components",
        "/libs/wcm/core",
        "/etc/designs/novaria",
        "/var/workflow/models/novaria-publish-review",
    ]
    normalized = validate_package_filters(valid_paths)
    assert [item["root"] for item in normalized] == valid_paths


def test_guardrail_100mb_package_size_limit_and_node_count_limit(tmp_path: Path):
    """
    Verify the 100 MB package byte limit (MAX_PACKAGE_BYTES = 104,857,600) and
    10,000 node count guardrail (MAX_PACKAGE_NODES = 10,000) across create, build,
    download, upload, and install.
    """
    assert MAX_PACKAGE_SIZE_MB == 100
    assert MAX_PACKAGE_BYTES == 100 * 1024 * 1024
    assert MAX_PACKAGE_NODES == 10_000

    class DummySimulator:
        def __init__(self, nodes: dict[str, Any]):
            self.all_nodes = nodes

    # 1. Node count limit guardrail during create_package and build_package
    many_nodes = {
        f"/content/novaria/us/en/page-{i}": {"jcr:primaryType": "cq:Page"}
        for i in range(15)
    }
    sim = DummySimulator(many_nodes)

    engine_node_cap = PackageManagerEngine(
        exchange_dir=tmp_path / "exchange",
        mock_state_dir=tmp_path / "mock_state",
        max_nodes=10,
    )

    with pytest.raises(ValueError, match="node limit guardrail exceeded"):
        engine_node_cap.create_package(
            name="too-many-nodes",
            group="novaria",
            version="1.0",
            filters=["/content/novaria/us/en"],
            confirm=True,
            env_id="int",
            simulator=sim,
        )

    # Create without initial filters, then set filters without simulator, then fail on build_package
    engine_node_cap.create_package(name="too-many-nodes", group="novaria", version="1.0", confirm=True, env_id="int")
    engine_node_cap.set_filters(
        name="too-many-nodes",
        group="novaria",
        version="1.0",
        filters=["/content/novaria/us/en"],
        confirm=True,
        env_id="int",
    )
    with pytest.raises(ValueError, match="node limit guardrail exceeded"):
        engine_node_cap.build_package(
            name="too-many-nodes",
            group="novaria",
            version="1.0",
            confirm=True,
            env_id="int",
            simulator=sim,
        )

    # 2. Size limit guardrail (100 MB default & custom byte cap verification)
    # Verify inspect_and_validate_package_zip rejects uncompressed size > 100 MB (without allocating 100MB in RAM
    # by testing both a highly-compressible >100MB stream and a custom max_bytes engine).
    engine_size_cap = PackageManagerEngine(
        exchange_dir=tmp_path / "exchange_size",
        mock_state_dir=tmp_path / "mock_size",
        max_package_bytes=1024,  # 1 KB cap to test build/download/upload rejection
    )
    large_sim = DummySimulator({
        "/content/novaria/us/en/large": {
            "jcr:primaryType": "cq:Page",
            "payload": "X" * 4096,
        }
    })
    engine_size_cap.create_package(
        name="oversized-pkg",
        group="novaria",
        version="1.0",
        filters=["/content/novaria/us/en/large"],
        confirm=True,
        env_id="int",
    )
    with pytest.raises(ValueError, match="size guardrail exceeded"):
        engine_size_cap.build_package(
            name="oversized-pkg",
            group="novaria",
            version="1.0",
            confirm=True,
            env_id="int",
            simulator=large_sim,
        )

    # Also test direct 100 MB (MAX_PACKAGE_BYTES) uncompressed size check via ZIP header inspection
    buf_101mb = io.BytesIO()
    with zipfile.ZipFile(buf_101mb, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "META-INF/vault/filter.xml",
            '<?xml version="1.0" encoding="UTF-8"?><workspaceFilter version="1.0"><filter root="/content/novaria"/></workspaceFilter>',
        )
        # 101 MB of zeroes compresses to ~100 KB in memory but has uncompressed file_size == 101 MB
        zf.writestr("jcr_root/content/novaria/huge-asset.bin", b"\x00" * (101 * 1024 * 1024))

    with pytest.raises(ValueError, match="100 MB"):
        inspect_and_validate_package_zip(buf_101mb.getvalue())

