#!/usr/bin/env python3
"""
Autonomous Agent Content Audit Simulation for AEM Content Intelligence MCP.

Demonstrates how an AI Assistant (e.g., Claude 3.7 Sonnet, ChatGPT) uses the
AEM Content Intelligence MCP tools to autonomously discover repository structure,
audit content integrity, reconcile authored pages against the canonical PMS database,
measure global localization coverage, materialize bounded SQLite datasets, and
export audit reports (.xlsx / .csv).

Target Domain: Novaria Hospitality Group (fictional domain: novariahotels.com)
"""

import sys
import json
import time
from pathlib import Path
from typing import Any

# Ensure src is on sys.path
ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from aem_mcp.server import (
    aem_querybuilder,
    aem_discover_properties,
    aem_find_references,
    aem_audit_content_integrity,
    aem_audit_cross_reference,
    aem_audit_localization_coverage,
    aem_compile_querybuilder_sql2,
    dataset_analyze,
    dataset_export,
    dataset_discard,
)
from aem_mcp.datasets.engine import materialize_dataset


# Ensure UTF-8 output encoding on Windows terminals
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Visual Formatting Helpers
CYAN = "\033[96m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
MAGENTA = "\033[95m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"


def print_banner():
    print(f"\n{BOLD}{CYAN}{'='*80}")
    print(f"  AEM CONTENT INTELLIGENCE MCP: AUTONOMOUS AGENT AUDIT SIMULATION")
    print(f"  Target: Novaria Hospitality Group (1,000 Hotels | 11,004 JCR Nodes)")
    print(f"{'='*80}{RESET}\n")


def print_turn_header(turn_num: int, title: str):
    print(f"\n{BOLD}{MAGENTA}[TURN {turn_num}] >>> {title}{RESET}")
    print(f"{DIM}{'-'*80}{RESET}")


def print_cot(thought: str):
    print(f"\n{CYAN}{BOLD}[* Agent Chain-of-Thought]{RESET}")
    print(f"{CYAN}{thought.strip()}{RESET}\n")


def print_tool_call(tool_name: str, args: dict[str, Any]):
    print(f"{GREEN}{BOLD}[> Invoking Tool]{RESET} {GREEN}{tool_name}{RESET}")
    args_str = json.dumps(args, indent=2)
    indented = "\n".join("    " + line for line in args_str.splitlines())
    print(f"{DIM}{indented}{RESET}")


def print_tool_result_summary(summary: str):
    print(f"\n{YELLOW}{BOLD}[# Observation & Evidence]{RESET}")
    print(f"{YELLOW}{summary.strip()}{RESET}")


def simulate_full_audit(quiet: bool = False, export_dir: Path | None = None) -> dict[str, Any]:
    """
    Executes the 6-phase end-to-end agent audit simulation.
    Returns structured results dictionary.
    """
    start_time = time.perf_counter()
    if not quiet:
        print_banner()

    # -------------------------------------------------------------------------
    # USER PROMPT
    # -------------------------------------------------------------------------
    user_prompt = (
        "Perform a comprehensive repository intelligence and governance audit of our "
        "Novaria Hospitality Group site trees. Inspect repository structure, identify "
        "any broken DAM assets, expired promotional Experience Fragments, or incomplete Content Fragments. "
        "Cross-reference our authored hotel pages against the canonical Property Master (PMS) to find star rating "
        "discrepancies, operating status mismatches, unverified amenity claims, and orphan pages. "
        "Measure multi-region localization coverage, aggregate all findings, and generate an audit workbook."
    )
    if not quiet:
        print(f"{BOLD}[USER REQUEST]{RESET}")
        print(f"\"{user_prompt}\"\n")

    # =========================================================================
    # TURN 1: Discovery & Schema Exploration
    # =========================================================================
    if not quiet:
        print_turn_header(1, "Repository Discovery & Dependency Tracing")
        print_cot(
            "The user needs a full governance audit of Novaria Hospitality Group content.\n"
            "First, I'll discover the repository layout under /content/novaria/us/en/hotels "
            "by sampling active pages and inspecting property schemas and references."
        )

    t1_args = {"path": "/content/novaria/us/en/hotels", "type": "cq:Page", "property": "hotelId", "p.limit": "5"}
    if not quiet:
        print_tool_call("aem_querybuilder", t1_args)

    t1_raw = aem_querybuilder(query=t1_args)
    t1_res = json.loads(t1_raw)
    total_hotels = t1_res["data"]["total"]
    sample_page = t1_res["data"]["hits"][0]["jcr:path"]

    # Discover properties and find references on sample hotel page
    disc_raw = aem_discover_properties(path="/content/novaria/us/en/hotels", sample_limit=20)
    disc_res = json.loads(disc_raw)
    ref_raw = aem_find_references(path=sample_page)
    ref_res = json.loads(ref_raw)

    if not quiet:
        print_tool_result_summary(
            f"QueryBuilder located {total_hotels:,} authored hotel pages under /content/novaria/us/en/hotels.\n"
            f"Schema inspection confirmed standard properties: {list(disc_res.get('properties', {}).keys())[:8]}...\n"
            f"Reference tracing on leaf hotel page '{sample_page}' identified {ref_res.get('reference_count')} inbound/outbound references "
            f"to templates and Content Fragments."
        )

    # =========================================================================
    # TURN 2: Content Integrity Audit (DAM Assets, Promos, Content Fragments)
    # =========================================================================
    if not quiet:
        print_turn_header(2, "Content Integrity & Asset Reference Verification")
        print_cot(
            "Next, I will scan the site tree for content integrity anomalies:\n"
            "1. Broken DAM image references in hero banners\n"
            "2. Expired promotional Experience Fragments linked on live pages\n"
            "3. Incomplete Content Fragments with missing mandatory marketing copy."
        )

    t2_args = {"root_path": "/content/novaria/us/en", "limit": 200}
    if not quiet:
        print_tool_call("aem_audit_content_integrity", t2_args)

    t2_raw = aem_audit_content_integrity(root_path="/content/novaria/us/en", limit=200)
    t2_res = json.loads(t2_raw)
    t2_summary = t2_res["summary"]

    if not quiet:
        print_tool_result_summary(
            f"Audited {t2_res['pages_audited']:,} pages under /content/novaria/us/en.\n"
            f"Found {t2_res['total_anomalies']} Content Integrity Anomalies:\n"
            f"  - Broken DAM Hero Assets: {t2_summary['broken_dam_references']} pages\n"
            f"  - Expired Promotional XFs: {t2_summary['expired_promos']} pages (referencing 2024/expired campaigns)\n"
            f"  - Incomplete Content Fragments: {t2_summary['incomplete_content_fragments']} fragments (missing dining/wellness copy)"
        )

    # =========================================================================
    # TURN 3: Dual-System Cross-Reference Audit (AEM vs Canonical PMS Master)
    # =========================================================================
    if not quiet:
        print_turn_header(3, "Dual-System Cross-Reference (AEM JCR vs PMS Registry)")
        print_cot(
            "Now I will cross-reference authored AEM pages with the canonical PMS SQLite database.\n"
            "This checks business truth vs marketing claims: star ratings, operating status, "
            "and amenity claims (e.g. Michelin dining or Rooftop pools not in PMS), plus orphan pages."
        )

    t3_args = {"path": "/content/novaria/us/en/hotels", "limit": 200}
    if not quiet:
        print_tool_call("aem_audit_cross_reference", t3_args)

    t3_raw = aem_audit_cross_reference(path="/content/novaria/us/en/hotels", limit=200)
    t3_res = json.loads(t3_raw)
    t3_summary = t3_res["summary"]

    if not quiet:
        print_tool_result_summary(
            f"Cross-referenced {t3_res['aem_pages_audited']:,} AEM hotel pages against {t3_res['master_properties_count']:,} canonical PMS records.\n"
            f"Identified {t3_res['total_discrepancies']} PMS Discrepancies:\n"
            f"  - Stale Star Ratings: {t3_summary['stale_star_ratings']} properties (authored rating != PMS truth)\n"
            f"  - Operating Status Mismatches: {t3_summary['operating_status_mismatches']} properties (Active in CMS vs Renovating in PMS)\n"
            f"  - Amenity Desyncs: {t3_summary['amenity_desyncs']} properties (unapproved marketing claims)\n"
            f"  - Orphan Legacy Pages: {t3_summary['orphan_pages']} pages (referencing decommissioned hotel IDs)"
        )

    # =========================================================================
    # TURN 4: Global Localization Coverage Audit
    # =========================================================================
    if not quiet:
        print_turn_header(4, "International Market Localization Parity Audit")
        print_cot(
            "Now I will measure translation parity across target international markets:\n"
            "Comparing canonical 'us/en' (1,000 properties) against 'gb/en', 'fr/fr', 'de/de', 'es/es', and 'jp/ja'."
        )

    t4_args = {"base_locale": "us/en", "target_locales": ["gb/en", "fr/fr", "de/de", "es/es", "jp/ja"], "subpath": "hotels", "limit": 100}
    if not quiet:
        print_tool_call("aem_audit_localization_coverage", t4_args)

    t4_raw = aem_audit_localization_coverage(base_locale="us/en", subpath="hotels", limit=100)
    t4_res = json.loads(t4_raw)
    locales = t4_res["locales"]

    total_missing_translations = sum(l["missing_count"] for l in locales.values())

    if not quiet:
        print_tool_result_summary(
            f"Localization coverage analysis across {len(locales)} international markets:\n"
            f"  - French (fr/fr): {locales['fr/fr']['existing_pages']:,}/{t4_res['canonical_pages']:,} pages "
            f"({locales['fr/fr']['coverage_pct']}%) | {locales['fr/fr']['missing_count']} MISSING translations\n"
            f"  - German (de/de): {locales['de/de']['existing_pages']:,}/{t4_res['canonical_pages']:,} pages "
            f"({locales['de/de']['coverage_pct']}%) | {locales['de/de']['missing_count']} MISSING translations\n"
            f"  - UK English (gb/en): {locales['gb/en']['coverage_pct']}% (Complete)\n"
            f"  - Spanish (es/es): {locales['es/es']['coverage_pct']}% (Complete)\n"
            f"  - Japanese (jp/ja): {locales['jp/ja']['coverage_pct']}% (Complete)\n"
            f"Total Missing Regional Variants: {total_missing_translations}"
        )

    # =========================================================================
    # TURN 5: Materialization & Server-Side In-Memory Aggregation
    # =========================================================================
    if not quiet:
        print_turn_header(5, "In-Memory Dataset Materialization & Analytics")
        print_cot(
            "To avoid overloading the LLM context window with 350 individual anomaly objects,\n"
            "I will materialize the full audit registry into an in-memory SQLite dataset\n"
            "and execute server-side SQL aggregation queries."
        )

    # Flatten all 350 anomalies into a unified audit record table
    all_audit_rows = []
    
    # 1. Integrity anomalies (130)
    for a in t2_res.get("anomalies", []):
        all_audit_rows.append({
            "category": "Content Integrity",
            "type": a["type"],
            "severity": a.get("severity", "MEDIUM"),
            "path": a.get("page") or a.get("fragment"),
            "details": a.get("issue", "")
        })
    
    # 2. Cross-reference discrepancies (120)
    for d in t3_res.get("discrepancies", []):
        all_audit_rows.append({
            "category": "Master Data Reconciliation",
            "type": d["type"],
            "severity": d.get("severity", "HIGH"),
            "path": d.get("page", ""),
            "details": d.get("issue", "")
        })

    # 3. Localization gaps (100)
    for locale_code, l_info in locales.items():
        for missing_path in l_info.get("sample_missing", []):
            all_audit_rows.append({
                "category": "Localization Parity",
                "type": f"MISSING_{locale_code.upper().replace('/', '_')}_TRANSLATION",
                "severity": "HIGH",
                "path": missing_path,
                "details": f"Missing translated page in {locale_code} market."
            })

    ds_id = materialize_dataset(
        all_audit_rows,
        metadata={"source": "novaria_agent_audit", "timestamp": time.time()}
    )

    t5_args_1 = {"dataset_id": ds_id, "operation": "group_by", "field": "category"}
    t5_args_2 = {"dataset_id": ds_id, "operation": "group_by", "field": "severity"}
    
    if not quiet:
        print_tool_call("materialize_dataset", {"row_count": len(all_audit_rows)})
        print_tool_call("dataset_analyze", t5_args_1)
        print_tool_call("dataset_analyze", t5_args_2)

    cat_analysis = json.loads(dataset_analyze(dataset_id=ds_id, operation="group_by", field="category"))
    sev_analysis = json.loads(dataset_analyze(dataset_id=ds_id, operation="group_by", field="severity"))

    if not quiet:
        print_tool_result_summary(
            f"Materialized {len(all_audit_rows)} findings into in-memory SQLite dataset '{ds_id}'.\n"
            f"Findings by Category: {json.dumps(cat_analysis.get('groups', {}), indent=2)}\n"
            f"Findings by Severity: {json.dumps(sev_analysis.get('groups', {}), indent=2)}"
        )

    # =========================================================================
    # TURN 6: Report Export & Executive Governance Dashboard
    # =========================================================================
    if not quiet:
        print_turn_header(6, "Audit Report Generation & Governance Summary")
        print_cot(
            "Finally, I will generate a downloadable standalone Excel (.xlsx) workbook and CSV report\n"
            "for the Novaria digital operations team, followed by the executive summary."
        )

    t6_args_1 = {"dataset_id": ds_id, "format": "xlsx"}
    t6_args_2 = {"dataset_id": ds_id, "format": "csv"}

    if not quiet:
        print_tool_call("dataset_export", t6_args_1)
        print_tool_call("dataset_export", t6_args_2)

    exp_xlsx = json.loads(dataset_export(dataset_id=ds_id, format="xlsx"))
    exp_csv = json.loads(dataset_export(dataset_id=ds_id, format="csv"))

    xlsx_path = Path(exp_xlsx["file_path"])
    csv_path = Path(exp_csv["file_path"])

    # Clean up SQLite dataset
    dataset_discard(ds_id)

    duration = round(time.perf_counter() - start_time, 2)

    if not quiet:
        print(f"\n{GREEN}{BOLD}=== EXECUTIVE GOVERNANCE AUDIT REPORT ==={RESET}")
        print(f"Domain:              Novaria Hospitality Group (novariahotels.com)")
        print(f"Total Pages Audited: {t1_res['data']['total'] + t3_res['aem_pages_audited']:,}")
        print(f"Audit Duration:      {duration}s")
        print(f"Excel Report (.xlsx): {xlsx_path} ({xlsx_path.stat().st_size:,} bytes)")
        print(f"                     Download URL: {exp_xlsx.get('download_url')}")
        print(f"CSV Report (.csv):   {csv_path} ({csv_path.stat().st_size:,} bytes)")
        print(f"                     Download URL: {exp_csv.get('download_url')}\n")

        print(f"{BOLD}{'Category':<32} | {'Severity':<10} | {'Count':<8} | {'Status'}{RESET}")
        print(f"{'-'*70}")
        print(f"{'Stale Star Ratings (vs PMS)':<32} | {'HIGH':<10} | {t3_summary['stale_star_ratings']:<8} | {YELLOW}Remediate Metadata{RESET}")
        print(f"{'Broken DAM Hero Assets':<32} | {'CRITICAL':<10} | {t2_summary['broken_dam_references']:<8} | {YELLOW}Replace Assets{RESET}")
        print(f"{'Expired Promotional Campaigns':<32} | {'HIGH':<10} | {t2_summary['expired_promos']:<8} | {YELLOW}Unpublish XFs{RESET}")
        print(f"{'Missing French Localizations':<32} | {'HIGH':<10} | {locales['fr/fr']['missing_count']:<8} | {YELLOW}Trigger Translation{RESET}")
        print(f"{'Missing German Localizations':<32} | {'HIGH':<10} | {locales['de/de']['missing_count']:<8} | {YELLOW}Trigger Translation{RESET}")
        print(f"{'Incomplete Content Fragments':<32} | {'MEDIUM':<10} | {t2_summary['incomplete_content_fragments']:<8} | {YELLOW}Authoring Review{RESET}")
        print(f"{'Amenity Desyncs (Marketing vs PMS)':<32} | {'HIGH':<10} | {t3_summary['amenity_desyncs']:<8} | {YELLOW}Reconcile Claims{RESET}")
        print(f"{'Operating Status Desyncs':<32} | {'CRITICAL':<10} | {t3_summary['operating_status_mismatches']:<8} | {YELLOW}Flag Renovations{RESET}")
        print(f"{'Orphan Legacy Pages':<32} | {'CRITICAL':<10} | {t3_summary['orphan_pages']:<8} | {YELLOW}Archive & Redirect{RESET}")
        print(f"{'-'*70}")
        print(f"{BOLD}{'TOTAL ANOMALIES IDENTIFIED':<32} | {'':<10} | {len(all_audit_rows):<8} | ACTION REQUIRED{RESET}\n")

    return {
        "duration_seconds": duration,
        "total_anomalies": len(all_audit_rows),
        "content_integrity_anomalies": t2_res["total_anomalies"],
        "pms_discrepancies": t3_res["total_discrepancies"],
        "missing_translations": total_missing_translations,
        "xlsx_path": str(xlsx_path),
        "csv_path": str(csv_path),
        "xlsx_download_url": exp_xlsx.get("download_url"),
        "csv_download_url": exp_csv.get("download_url")
    }


if __name__ == "__main__":
    simulate_full_audit()
