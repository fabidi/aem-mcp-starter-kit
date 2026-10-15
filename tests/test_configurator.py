"""
Unit and integration tests for the ZohoMCP-style AEM MCP Instance, Role & Environment Configurator.
"""

import json
import os
import subprocess
import sys
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from aem_mcp.configurator import (
    ALL_TOOL_NAMES,
    DEFAULT_ENVIRONMENTS,
    ROLE_PRESETS,
    TOOL_CATALOG,
    TOOL_CATEGORIES,
    ConfiguratorStore,
    create_configurator_handler,
)


def test_default_environments_and_catalog(tmp_path: Path):
    """Verify initial environments (dev, int, qa, stg, prod) and 28-tool catalog."""
    store = ConfiguratorStore(profiles_dir=tmp_path / "profiles")

    assert list(store.environments.keys()) == ["dev", "int", "qa", "stg", "prod"]
    assert store.environments["dev"]["mode"] == "mock"
    assert store.environments["int"]["mode"] == "onprem"
    assert store.environments["qa"]["mode"] == "cloud"
    assert store.environments["prod"]["enable_registry"] is False

    assert len(TOOL_CATEGORIES) == 7
    assert len(TOOL_CATALOG) == 35
    assert len(ALL_TOOL_NAMES) == 35


def test_create_instance_from_scratch_and_from_preset(tmp_path: Path):
    """Verify creating custom MCP configs both from scratch (blank) and from role presets."""
    store = ConfiguratorStore(profiles_dir=tmp_path / "profiles")

    # 1. Create from scratch (blank preset -> 0 tools initially)
    blank_inst = store.upsert_instance({
        "id": "aem-scratch-qa",
        "name": "Custom Scratch QA Agent",
        "role_preset": "blank",
        "environment": "qa",
    })
    assert blank_inst["allowed_tools"] == []
    assert (tmp_path / "profiles" / "aem-scratch-qa.json").exists()

    # 2. Customize the scratch instance with 3 hand-picked tools
    custom_inst = store.upsert_instance({
        "id": "aem-scratch-qa",
        "name": "Custom Scratch QA Agent",
        "role_preset": "blank",
        "environment": "qa",
        "allowed_tools": ["aem_json", "aem_querybuilder", "aem_lint_query_indexing"],
    })
    assert custom_inst["allowed_tools"] == ["aem_json", "aem_querybuilder", "aem_lint_query_indexing"]

    # 3. Create from a role preset (content-author on stg)
    author_stg = store.upsert_instance({
        "id": "aem-author-stg",
        "name": "Content Author (STG)",
        "role_preset": "content-author",
        "environment": "stg",
    })
    assert set(author_stg["allowed_tools"]) == set(ROLE_PRESETS["content-author"]["tools"])
    assert "execute_code" not in author_stg["allowed_tools"]
    assert "aem_lint_query_indexing" not in author_stg["allowed_tools"]


def test_batch_clone_role_across_environments(tmp_path: Path):
    """Verify generating multiple instances of the same role across dev, int, qa, stg, prod."""
    store = ConfiguratorStore(profiles_dir=tmp_path / "profiles")

    # Add a custom UAT environment too
    store.upsert_environment({
        "id": "uat",
        "name": "UAT",
        "mode": "cloud",
        "aem_url": "https://author-p10001-e20004.adobeaemcloud.com",
        "enable_registry": False,
    })

    envs = ["dev", "int", "qa", "stg", "uat", "prod"]
    created = store.clone_role_across_environments(
        base_id_prefix="aem-lead-dev",
        name_prefix="Lead Developer",
        role_preset="developer",
        environment_ids=envs,
        disable_sandbox_in_prod=True,
    )

    assert len(created) == 6
    created_ids = [c["id"] for c in created]
    assert created_ids == [
        "aem-lead-dev-dev",
        "aem-lead-dev-int",
        "aem-lead-dev-qa",
        "aem-lead-dev-stg",
        "aem-lead-dev-uat",
        "aem-lead-dev-prod",
    ]

    # Non-prod instances keep execute_code; prod instance strips execute_code
    assert "execute_code" in store.instances["aem-lead-dev-dev"]["allowed_tools"]
    assert "execute_code" in store.instances["aem-lead-dev-stg"]["allowed_tools"]
    assert "execute_code" not in store.instances["aem-lead-dev-prod"]["allowed_tools"]

    # Verify client config generation for those instances
    client_cfg = store.generate_client_config(created_ids)
    assert set(client_cfg["mcpServers"].keys()) == set(created_ids)
    assert client_cfg["mcpServers"]["aem-lead-dev-stg"]["env"]["AEM_MCP_ENV"] == "stg"
    assert client_cfg["mcpServers"]["aem-lead-dev-prod"]["env"]["AEM_URL"] == DEFAULT_ENVIRONMENTS["prod"]["aem_url"]


def test_configurator_http_server_endpoints(tmp_path: Path):
    """Test the local ZohoMCP-style HTTP Configurator UI and JSON API endpoints."""
    store = ConfiguratorStore(profiles_dir=tmp_path / "profiles")
    handler = create_configurator_handler(store)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = server.server_address[1]
    base_url = f"http://127.0.0.1:{port}"

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        # 1. GET / serves the HTML UI
        with urllib.request.urlopen(f"{base_url}/", timeout=5) as resp:
            assert resp.status == 200
            html = resp.read().decode("utf-8")
            assert "AEM MCP Configurator" in html
            assert "Batch Generate Role" in html

        # 2. GET /api/state returns full state
        with urllib.request.urlopen(f"{base_url}/api/state", timeout=5) as resp:
            state = json.loads(resp.read().decode("utf-8"))
            assert "environments" in state
            assert "instances" in state
            assert len(state["tools"]) == 35

        # 3. POST /api/environments creates a new environment
        req = urllib.request.Request(
            f"{base_url}/api/environments",
            data=json.dumps({
                "id": "perf",
                "name": "Performance Testing",
                "mode": "onprem",
                "aem_url": "http://localhost:4504",
                "enable_registry": False,
            }).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            res = json.loads(resp.read().decode("utf-8"))
            assert res["success"] is True
            assert "perf" in res["state"]["environments"]

        # 4. POST /api/instances creates a custom instance on 'perf'
        req = urllib.request.Request(
            f"{base_url}/api/instances",
            data=json.dumps({
                "id": "aem-dev-perf",
                "name": "AEM Developer (PERF)",
                "role_preset": "developer",
                "environment": "perf",
                "allowed_tools": ["aem_json", "aem_querybuilder", "aem_lint_query_indexing"],
            }).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            res = json.loads(resp.read().decode("utf-8"))
            assert res["success"] is True
            assert res["instance"]["allowed_tools"] == ["aem_json", "aem_querybuilder", "aem_lint_query_indexing"]
    finally:
        server.shutdown()
        server.server_close()


def test_runtime_server_scoping_enforces_authorized_tools():
    """
    Verify that starting the MCP server with a specific instance or CLI flag
    strictly registers ONLY the tools authorized for that instance.
    """
    code = (
        "import asyncio, json\n"
        "from aem_mcp.server import mcp, aem_runtime_resource, aem_environment_resource\n"
        "tools = asyncio.run(mcp.list_tools())\n"
        "names = sorted([t.name for t in tools])\n"
        "runtime = json.loads(aem_runtime_resource())\n"
        "env_info = json.loads(aem_environment_resource())\n"
        "print(json.dumps({'tools': names, 'runtime': runtime, 'env': env_info}))\n"
    )

    # 1. Test Content Author PROD instance via AEM_MCP_INSTANCE env var
    env_author = os.environ.copy()
    env_author["AEM_MCP_INSTANCE"] = "aem-author-prod"
    proc_author = subprocess.run(
        [sys.executable, "-c", code],
        env=env_author,
        capture_output=True,
        text=True,
        check=True,
    )
    payload_author = json.loads(proc_author.stdout.strip())
    assert set(payload_author["tools"]) == set(ROLE_PRESETS["content-author"]["tools"])
    assert len(payload_author["tools"]) == 9
    assert "execute_code" not in payload_author["tools"]
    assert "aem_lint_query_indexing" not in payload_author["tools"]
    assert payload_author["runtime"]["active_instance"] == "aem-author-prod"
    assert payload_author["runtime"]["environment_tier"] == "prod"
    assert payload_author["env"]["environment_id"] == "prod"

    # 2. Test Developer PROD vs Developer STG via CLI --instance flag
    proc_dev_prod = subprocess.run(
        [sys.executable, "-c", code, "--instance", "aem-dev-prod"],
        capture_output=True,
        text=True,
        check=True,
    )
    payload_dev_prod = json.loads(proc_dev_prod.stdout.strip())
    assert "aem_lint_query_indexing" in payload_dev_prod["tools"]
    assert "aem_compile_querybuilder_sql2" in payload_dev_prod["tools"]
    assert "package_list" in payload_dev_prod["tools"]
    assert "package_download" in payload_dev_prod["tools"]
    assert "package_create" not in payload_dev_prod["tools"]
    assert "package_install" not in payload_dev_prod["tools"]
    assert "execute_code" not in payload_dev_prod["tools"]
    assert len(payload_dev_prod["tools"]) == 18

    proc_dev_stg = subprocess.run(
        [sys.executable, "-c", code, "--instance", "aem-dev-stg"],
        capture_output=True,
        text=True,
        check=True,
    )
    payload_dev_stg = json.loads(proc_dev_stg.stdout.strip())
    assert "execute_code" in payload_dev_stg["tools"]
    assert "package_create" in payload_dev_stg["tools"]
    assert "package_install" in payload_dev_stg["tools"]
    assert len(payload_dev_stg["tools"]) == 24
    assert payload_dev_stg["runtime"]["environment_tier"] == "stg"
    assert payload_dev_stg["env"]["environment_id"] == "stg"
