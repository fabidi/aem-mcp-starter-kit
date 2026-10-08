# AEM MCP Starter Kit 🚀

> **The Open-Source Model Context Protocol (MCP) Server for Adobe Experience Manager (AEM Sites, Content Fragments & DAM Assets).**  
> *Demonstrating Agentic RAG over Native Structured Systems vs. Brittle Vector Databases.*

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue.svg)](https://www.python.org/downloads/)
[![CI](https://github.com/fabidi/aem-mcp-starter-kit/actions/workflows/ci.yml/badge.svg)](https://github.com/fabidi/aem-mcp-starter-kit/actions/workflows/ci.yml)
[![FastMCP](https://img.shields.io/badge/FastMCP-Enabled-green.svg)](https://gofastmcp.com/)

---

## Why AEM MCP?

Most enterprise generative AI tutorials force-fit unstructured vector databases (Pinecone, Chroma, embeddings) onto structured CMS content. In Adobe Experience Manager (AEM), where content is already structured hierarchically (JCR), typed (`cq:Page`, Content Fragment Models), and indexed via QueryBuilder, **vector search is often an anti-pattern**:
- Vectors cannot evaluate template rules or resource types (`sling:resourceType`).
- Vectors cannot detect missing language copies or broken asset paths.
- Vectors cannot reconcile authored marketing claims against canonical enterprise systems of record.

By exposing AEM's native JCR content representations and QueryBuilder predicates through a thin, read-only **Model Context Protocol (MCP)** server, frontier models (Claude 3.7 Sonnet, GPT-5, Cursor, ChatGPT Work) can autonomously investigate, audit, and govern enterprise repositories with zero hallucination.

```mermaid
flowchart TD
    Client["AI Agent / LLM Client<br/>(Claude Desktop / Cursor / ChatGPT)"]
    MCP["FastMCP Server: AEM Content Intelligence<br/>(Read-Only Guardrails, Pagination & In-Memory Datasets)"]

    subgraph Adapters ["Dual-Mode Storage & Service Adapters"]
        LiveAEM["Live AEM Client<br/>(AEMaaCS / On-Prem 6.5)<br/>• Sling JSON Selectors<br/>• /bin/querybuilder.json<br/>• Basic / Adobe IMS OAuth"]
        MockJCR["Virtual JCR Simulator<br/>(Local In-Memory / File-backed)<br/>• Evaluates QueryBuilder Predicates<br/>• Resolves .json & .1.json<br/>• 110+ Synthetic Properties"]
        EntityMaster["Master Entity Registry<br/>(SQLite / PIM / CRS)<br/>• Canonical Property Master<br/>• Brands, Amenities, Geocodes"]
    end

    Client <-->|"Model Context Protocol"| MCP
    MCP <-->|"Live Mode (AEM_MODE=live)"| LiveAEM
    MCP <-->|"Mock Mode (AEM_MODE=mock)"| MockJCR
    MCP <-->|"Both Modes"| EntityMaster
```

---

> [!NOTE]
> **Fictional Demo Domain Notice:**
> **"Novaria Hospitality Group"** (`novariahotels.com`) and all associated brand tiers (*Novaria Grand*, *Novaria House*, *Solstice Resorts*, *Novaria Select*), property names, personnel, and geolocations are 100% fictional demo entities created strictly for testing, agentic RAG evaluation, and open-source demonstration. Any resemblance to actual hospitality companies, operating properties, or registered trademarks is purely coincidental.

## Key Features

1. **Zero-Cost Offline JCR Simulator:**
   - Clone and run immediately on your laptop without needing an expensive Adobe Cloud license.
   - Includes 1,000 canonical synthetic properties across 50 international destinations, 11,000+ JCR nodes, and 350 deliberate audit anomalies under the fictional **Novaria Hospitality Group** demo domain.
2. **Dual-System Reconciliation (AEM CMS + Master Entity Registry):**
   - Combines authored marketing content (AEM JCR) with canonical business truths (Property Master database) to pinpoint discrepancies (e.g. stale star ratings, outdated amenities).
3. **In-Memory SQLite Dataset Materialization:**
   - Instead of blowing up the LLM's context window with thousands of raw JSON nodes, the server streams query hits into local SQLite tables, performs server-side analytics, and generates downloadable **Excel (.xlsx)** and **CSV** audit workbooks.
4. **Cloud & On-Premises Ready:**
   - Seamlessly switch between Local Simulator (`AEM_MODE=mock`), On-Prem 6.5 (`AEM_MODE=onprem`), and AEM as a Cloud Service (`AEM_MODE=cloud` with Adobe IMS OAuth 2.0).

---

## 60-Second Quickstart

### 1. Installation
Clone the repository and install with `uv` (recommended) or `pip`:

```bash
git clone https://github.com/fabidi/aem-mcp-starter-kit.git
cd aem-mcp-starter-kit

# Using uv (fastest)
uv venv
uv pip install -e ".[dev]"

# Or standard pip
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

### 2. Generate Synthetic Seed Data (Mock Mode)
```bash
python tools/generate_dataset.py
```
This generates:
- `data/property_master.sqlite` (1,000 canonical hotels, 4 brand tiers, 50 global destinations)
- `data/jcr_mock_store.json` (11,000+ JCR nodes with 350 deliberate audit anomalies across 9 categories)

### 3. Run Autonomous Agent Audit Simulation
Simulate how an autonomous AI Assistant (e.g. Claude 3.7 or ChatGPT) executes an end-to-end 6-phase content intelligence audit across 11,000+ nodes, reconciles PMS records, and generates an Excel report in under 2 seconds:

```bash
python tools/simulate_agent_audit.py
```

### 4. Run the Test Suite
```bash
pytest -v
```

---

## Connecting to AI Clients

### Antigravity IDE Configuration
Add to your Antigravity MCP configuration (`~/.gemini/antigravity/mcp_config.json`):

```json
{
  "mcpServers": {
    "aem": {
      "command": "uv",
      "args": [
        "--directory",
        "/absolute/path/to/aem-mcp-starter-kit",
        "run",
        "aem-mcp"
      ],
      "env": {
        "AEM_MODE": "mock"
      }
    }
  }
}
```

### Claude Desktop Configuration
Add the server to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "aem-content-intelligence": {
      "command": "uv",
      "args": [
        "--directory",
        "/absolute/path/to/aem-mcp-starter-kit",
        "run",
        "aem-mcp"
      ],
      "env": {
        "AEM_MODE": "mock"
      }
    }
  }
}
```

### Cursor / Windsurf Configuration
Add to your project's `.cursor/mcp.json`:
```json

{
  "mcpServers": {
    "aem": {
      "command": "python",
      "args": ["-m", "aem_mcp.server"],
      "cwd": "/absolute/path/to/aem-mcp-starter-kit",
      "env": {
        "AEM_MODE": "mock"
      }
    }
  }
}
```

---

## Tool Reference

### Master Entity Registry Tools
| Tool | Description |
| :--- | :--- |
| `registry_indexes()` | List available business entity tables (`properties`, `brands`, `destinations`). |
| `registry_search(index, query, city, brand, limit)` | Search canonical property and brand records with structured filters. |
| `registry_get(index, identifier)` | Lookup canonical record by primary ID (`hotel_id` or `brand_id`). |

### AEM Read-Only Tools
| Tool | Description |
| :--- | :--- |
| `aem_json(path, depth)` | Read raw JCR node hierarchy using standard Sling `.json` selectors (depth 0..3). |
| `aem_traverse(path, depth)` | Explore child nodes and descendant levels. |
| `aem_querybuilder(query)` | Execute bounded AEM QueryBuilder predicate searches with execution timing profiling. |
| `aem_find_references(path)` | Recursively discover referenced templates, models, Content Fragments, and assets. |
| `aem_discover_properties(path, focus_term, node_type)` | Discover property usage and observed values across child nodes. |
| `aem_query_dataset(query, properties)` | Materialize large QueryBuilder search results into an in-memory SQLite table. |

### Content & Discrepancy Auditing Tools
| Tool | Description |
| :--- | :--- |
| `aem_audit_content_integrity(root_path)` | Scan content trees for broken DAM hero assets, expired promo campaigns, and incomplete Content Fragments. |
| `aem_audit_cross_reference(path)` | Reconcile AEM authored properties against Canonical PMS database (star ratings, booking status, amenity desyncs, orphan pages). |
| `aem_audit_localization_coverage(base_locale, target_locales)` | Measure regional translation parity and pinpoint missing localized pages across target locales (e.g. `fr/fr`, `de/de`, `jp/ja`). |
| `aem_compile_querybuilder_sql2(query)` | Compile QueryBuilder predicates into Oak-optimized JCR-SQL2 for index tuning and developer analysis. |

### Dataset Analysis & Export Tools
| Tool | Description |
| :--- | :--- |
| `dataset_info(dataset_id)` | View row counts and metadata of a materialized query dataset. |
| `dataset_get_rows(dataset_id, offset, limit)` | Read a bounded slice of rows. |
| `dataset_analyze(dataset_id, operation, field)` | Perform server-side `group_by`, `count`, `missing`, or `filter` aggregations. |
| `dataset_match(left_id, right_id, left_field, right_field)` | Cross-system reconciliation between AEM content and Property Master. |
| `dataset_export(dataset_id, format)` | Generate a downloadable CSV or standalone Excel (`.xlsx`) audit workbook. |
| `dataset_discard(dataset_id)` | Clean up temporary dataset tables. |

---

## Sample Agent Prompts to Try

Once connected to Claude or ChatGPT:

1. **Content Discovery:**
   > *"Find all luxury hotels in Europe under `/content/novaria` and show me their template and Content Fragment references."*

2. **Cross-System Audit:**
   > *"Audit our European property pages against the Master Entity Registry. Are there any discrepancies between the authored star ratings on AEM pages and the canonical Property Master?"*

3. **Campaign Expiration Check:**
   > *"Search across all hotel detail pages for any promotional Experience Fragment references that link to expired 2024 campaigns."*

4. **Spreadsheet Report Generation:**
   > *"Query all properties in the system, extract their hotel ID, authored rating, and city, and export the findings as an Excel (.xlsx) file."*

---

## License
MIT License. Created by Faisal Abidi.
