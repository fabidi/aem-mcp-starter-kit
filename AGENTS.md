# AGENTS.md — Agent-Led Integration Protocol 🤖

> **Operational manual for AI Agents (Claude Code, Cursor, Windsurf, Antigravity, Cline) integrating this MCP server into enterprise environments.**

---

## Mission

You are an Autonomous Integration Agent. Your goal is to connect this AEM Content Intelligence MCP server to a developer's live (or staged) Adobe Experience Manager (AEM) repository and their canonical Master System of Record (ERP, PIM, CRM, or database), and verify cross-system content governance audits.

---

## 5-Phase Agent Integration Protocol

### Phase 1: Environment & Connectivity Diagnostics
Check the operating mode and verify connectivity:
1. Inspect runtime environment: Read MCP resource `aem://runtime` or check `AEM_MODE` environment variable.
2. Modes:
   - `AEM_MODE=mock`: Uses local synthetic simulator (no live AEM required).
   - `AEM_MODE=onprem`: Connects to live AEM 6.5 / On-Prem via `AEM_URL`, `AEM_USERNAME`, `AEM_PASSWORD`.
   - `AEM_MODE=cloud`: Connects to AEM as a Cloud Service via `AEM_URL`, `AEM_IMS_CLIENT_ID`, `AEM_IMS_CLIENT_SECRET`, `AEM_IMS_ORG_ID`.
3. Verify basic connectivity by running `aem_querybuilder(query={"path": "/content", "p.limit": "1"})`.

---

### Phase 2: Live JCR Content Introspection
Do not guess the client's content hierarchy. Discover it dynamically:
1. **Find Site Roots:**
   Call `aem_traverse(path="/content", depth=1)` to locate client brand paths (e.g. `/content/client-brand/us/en`).
2. **Discover Authored Properties:**
   Call `aem_discover_properties(path="/content/client-brand/us/en", node_type="cq:Page", sample_limit=25)`.
   Inspect the observed properties to identify:
   - The **Foreign Key Identifier** (e.g. `sku`, `productId`, `hotelId`, `vinPrefix`, `policyNumber`).
   - The **Business Attributes** (e.g. `price`, `msrp`, `starRating`, `status`, `disclaimer`).
   - The **DAM Asset Paths** (e.g. `heroAsset/fileReference`, `thumbnail`).

---

### Phase 3: Scaffold the Client Domain Profile
Create a custom domain profile without modifying Python code:
1. Copy the blueprint template:
   ```bash
   cp -r domains/_template domains/<client_domain_id>
   ```
2. Place the client's master data file in `domains/<client_domain_id>/` (e.g. `master.csv`, `catalog.json`, `data.sqlite`).
3. Edit `domains/<client_domain_id>/domain.yaml`:
   - Set `domain.name` and `domain.root_path` (from Phase 2).
   - Set `entity.type` (`sqlite`, `csv`, or `json`) and `entity.file`.
   - Under `mapping.fields`: map registry columns to discovered JCR properties.
   - Under `audit_rules`: define compliance checks (e.g. `aem.authoredPrice != registry.price`).

---

### Phase 4: Dynamic Hot-Swap & Introspection
Activate and verify the new domain:
1. Call `domain_switch(name="<client_domain_id>")`.
2. Call `domain_get_active()` to confirm that table schemas, field mappings, and audit rules loaded successfully.
3. Call `registry_indexes()` to confirm canonical records and table row counts.

---

### Phase 5: Execute and Verify Cross-System Audits
Verify the integration with an end-to-end audit:
1. **Option A: Declarative Audit:**
   Call `aem_audit_domain_rules()` to evaluate all YAML rules across the site tree.
2. **Option B: Bulk Dataset Reconciliation:**
   - Stream AEM pages: `aem_query_dataset(query={"path": "<root_path>", "type": "cq:Page"}, properties=["<key>", "<field>"])` $\rightarrow$ `ds_aem`
   - Stream Master data: `registry_query_dataset(index="<table_name>")` $\rightarrow$ `ds_master`
   - Reconcile: `dataset_match(left_dataset_id="ds_aem", right_dataset_id="ds_master", left_field="<jcr_key>", right_field="<reg_key>")`
   - Export: `dataset_export(dataset_id="<match_id>", format="xlsx")` $\rightarrow$ returns download link.
3. Run `pytest -v` to ensure zero regressions across the MCP tool suite.

---

## Best Practices for Autonomous Agents

1. **Context Window Protection:**
   NEVER dump thousands of raw JCR hits into the chat. Always use `aem_query_dataset` and `registry_query_dataset` to stream into disk-backed SQLite datasets.
2. **Oak Traversal Safety:**
   Before running complex QueryBuilder searches over large paths, run `aem_lint_query_indexing(query)` to ensure the query is index-backed and will not trigger a 10,000-node Oak traversal scan abort.
3. **Structured Reporting:**
   Always report discrepancies with exact path, authored value, master value, and severity level.
