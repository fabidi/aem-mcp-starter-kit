"""
Content Audit & Discrepancy Analysis Engine for AEM MCP.

Provides specialized data auditing capabilities to detect:
1. Content Integrity Anomalies (broken DAM references, expired campaigns, incomplete CFs).
2. Cross-System Discrepancies (AEM authored values vs Canonical Master Entity Registry).
3. Localization Parity (missing regional translations and coverage metrics).
"""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from aem_mcp.config import CONFIG


def audit_content_integrity(
    node_getter: Callable[[str], dict[str, Any] | None],
    root_path: str = "/content/novaria",
    check_assets: bool = True,
    check_promos: bool = True,
    check_fragments: bool = True,
    limit: int = 100,
    all_nodes: dict[str, Any] | None = None
) -> dict[str, Any]:
    """
    Audit AEM pages under root_path for broken references, expired campaigns, and incomplete fragments.
    """
    limit = min(max(int(limit), 1), 500)
    anomalies: list[dict[str, Any]] = []

    # If all_nodes dictionary is available (e.g. from simulator), use it directly for ultra-fast scans
    if all_nodes is not None:
        target_pages = [
            (path, node) for path, node in all_nodes.items()
            if (path == root_path or path.startswith(root_path + "/"))
            and node.get("jcr:primaryType") == "cq:Page"
        ]
    else:
        # Fallback to single root retrieval
        root_data = node_getter(root_path)
        target_pages = [(root_path, root_data)] if root_data else []

    counts = {
        "broken_dam_references": 0,
        "unique_broken_assets": 0,
        "expired_promos": 0,
        "unique_expired_promos": 0,
        "incomplete_content_fragments": 0,
        "unique_incomplete_fragments": 0
    }
    unique_broken_assets = set()
    unique_expired_promos = set()
    unique_incomplete_fragments = set()

    now_iso = datetime.now(timezone.utc).date().isoformat()

    for page_path, page_node in target_pages:
        if not page_node:
            continue
        content = page_node.get("jcr:content", {})
        if not isinstance(content, dict):
            continue

        root_container = content.get("root", {})
        if not isinstance(root_container, dict):
            continue

        # 1. Check Broken DAM Assets
        if check_assets:
            hero = root_container.get("hero", {})
            if isinstance(hero, dict):
                hero_ref = hero.get("fileReference")
                if hero_ref and isinstance(hero_ref, str) and hero_ref.startswith("/content/dam/"):
                    asset_node = all_nodes.get(hero_ref) if all_nodes is not None else node_getter(hero_ref)
                    if asset_node is None:
                        counts["broken_dam_references"] += 1
                        unique_broken_assets.add(hero_ref)
                        anomalies.append({
                            "type": "BROKEN_DAM_REFERENCE",
                            "severity": "HIGH",
                            "page": page_path,
                            "component": "hero",
                            "referenced_path": hero_ref,
                            "issue": f"Referenced DAM hero asset does not exist in repository: {hero_ref}"
                        })

        # 2. Check Expired Promo Experience Fragments
        if check_promos:
            promo = root_container.get("promo_banner", {})
            if isinstance(promo, dict):
                promo_ref = promo.get("fragmentPath")
                if promo_ref and isinstance(promo_ref, str):
                    promo_node = all_nodes.get(promo_ref) if all_nodes is not None else node_getter(promo_ref)
                    if promo_node:
                        p_content = promo_node.get("jcr:content", {})
                        status = p_content.get("campaignStatus", "")
                        valid_through = p_content.get("validThrough", "")
                        is_expired = (status == "expired") or (valid_through and valid_through < now_iso)
                        if is_expired:
                            counts["expired_promos"] += 1
                            unique_expired_promos.add(promo_ref)
                            anomalies.append({
                                "type": "EXPIRED_CAMPAIGN_REFERENCE",
                                "severity": "MEDIUM",
                                "page": page_path,
                                "component": "promo_banner",
                                "referenced_path": promo_ref,
                                "discount_code": p_content.get("discountCode", ""),
                                "valid_through": valid_through,
                                "campaign_status": status,
                                "issue": f"Authored page links to expired campaign '{promo_ref}' (validThrough: {valid_through})"
                            })

        # 3. Check Incomplete Content Fragments
        if check_fragments:
            cf_comp = root_container.get("cf_component", {})
            if isinstance(cf_comp, dict):
                cf_path = cf_comp.get("fragmentPath")
                if cf_path and isinstance(cf_path, str):
                    cf_node = all_nodes.get(cf_path) if all_nodes is not None else node_getter(cf_path)
                    if cf_node:
                        cf_data = cf_node.get("jcr:content", {}).get("data", {}).get("master", {})
                        missing_fields = []
                        if not cf_data.get("primaryPhone"):
                            missing_fields.append("primaryPhone")
                        if not cf_data.get("contactEmail"):
                            missing_fields.append("contactEmail")
                        if missing_fields:
                            counts["incomplete_content_fragments"] += 1
                            unique_incomplete_fragments.add(cf_path)
                            anomalies.append({
                                "type": "INCOMPLETE_CONTENT_FRAGMENT",
                                "severity": "MEDIUM",
                                "page": page_path,
                                "fragment_path": cf_path,
                                "missing_fields": missing_fields,
                                "issue": f"Content Fragment '{cf_path}' lacks mandatory contact fields: {', '.join(missing_fields)}"
                            })

    counts["unique_broken_assets"] = len(unique_broken_assets)
    counts["unique_expired_promos"] = len(unique_expired_promos)
    counts["unique_incomplete_fragments"] = len(unique_incomplete_fragments)

    return {
        "root_path": root_path,
        "pages_audited": len(target_pages),
        "total_anomalies": len(anomalies),
        "summary": counts,
        "anomalies": anomalies[:limit]
    }


def audit_cross_reference(
    node_getter: Callable[[str], dict[str, Any] | None],
    path: str = "/content/novaria/us/en/hotels",
    db_path: Path | str | None = None,
    limit: int = 100,
    all_nodes: dict[str, Any] | None = None
) -> dict[str, Any]:
    """
    Reconcile AEM authored properties against Canonical Master Entity Registry (PMS database).
    Detects:
    - Stale star ratings
    - Operating status mismatches (Active vs Renovating)
    - Amenity desyncs (marketing claims vs PMS truth)
    - Orphaned legacy pages
    """
    limit = min(max(int(limit), 1), 500)
    db_file = Path(db_path) if db_path else CONFIG.property_master_db
    if not db_file.exists():
        raise FileNotFoundError(f"Master Entity Registry database not found at {db_file}")

    # Load canonical hotels from SQLite
    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        rows = cur.execute("SELECT * FROM properties").fetchall()
        master_hotels = {}
        for r in rows:
            rec = dict(r)
            if isinstance(rec.get("amenities"), str):
                try:
                    rec["amenities"] = json.loads(rec["amenities"])
                except Exception:
                    rec["amenities"] = []
            master_hotels[rec["hotel_id"]] = rec
    finally:
        conn.close()

    # Discover authored pages under path
    if all_nodes is not None:
        authored_pages = [
            (p, node) for p, node in all_nodes.items()
            if (p == path or p.startswith(path + "/"))
            and node.get("jcr:primaryType") == "cq:Page"
            and "hotelId" in node.get("jcr:content", {})
        ]
    else:
        authored_pages = []

    discrepancies: list[dict[str, Any]] = []
    counts = {
        "stale_star_ratings": 0,
        "operating_status_mismatches": 0,
        "amenity_desyncs": 0,
        "orphan_pages": 0,
        "unmapped_master_properties": 0
    }

    seen_master_ids = set()

    for page_path, page_node in authored_pages:
        content = page_node.get("jcr:content", {})
        hotel_id = content.get("hotelId")
        if not hotel_id:
            continue

        master = master_hotels.get(hotel_id)
        if not master:
            counts["orphan_pages"] += 1
            discrepancies.append({
                "type": "ORPHAN_PAGE",
                "severity": "CRITICAL",
                "page": page_path,
                "hotel_id": hotel_id,
                "issue": f"AEM page '{page_path}' references hotelId '{hotel_id}' which does not exist in Master Entity Registry."
            })
            continue

        seen_master_ids.add(hotel_id)

        # 1. Star Rating Mismatch
        authored_rating = content.get("authoredStarRating")
        master_rating = master.get("star_rating")
        if authored_rating is not None and master_rating is not None and authored_rating != master_rating:
            counts["stale_star_ratings"] += 1
            discrepancies.append({
                "type": "STALE_STAR_RATING",
                "severity": "HIGH",
                "page": page_path,
                "hotel_id": hotel_id,
                "hotel_name": master.get("name"),
                "authored_rating": authored_rating,
                "canonical_rating": master_rating,
                "issue": f"AEM star rating ({authored_rating}★) does not match Canonical Master Registry ({master_rating}★)."
            })

        # 2. Operating Status Mismatch
        authored_status = content.get("bookingStatus")
        master_status = master.get("status")
        if authored_status and master_status and authored_status.lower() != master_status.lower():
            counts["operating_status_mismatches"] += 1
            discrepancies.append({
                "type": "OPERATING_STATUS_MISMATCH",
                "severity": "CRITICAL",
                "page": page_path,
                "hotel_id": hotel_id,
                "hotel_name": master.get("name"),
                "authored_status": authored_status,
                "canonical_status": master_status,
                "issue": f"AEM bookingStatus is '{authored_status}', but PMS Master Status is '{master_status}' (potential booking compliance risk)."
            })

        # 3. Amenity Desyncs
        authored_amenities = set(content.get("authoredAmenities") or [])
        master_amenities = set(master.get("amenities") or [])
        unverified_amenities = sorted(list(authored_amenities - master_amenities))
        if unverified_amenities:
            counts["amenity_desyncs"] += 1
            discrepancies.append({
                "type": "AMENITY_DESYNC",
                "severity": "HIGH",
                "page": page_path,
                "hotel_id": hotel_id,
                "hotel_name": master.get("name"),
                "unverified_amenities": unverified_amenities,
                "issue": f"AEM claims unverified amenities not present in PMS master registry: {', '.join(unverified_amenities)}"
            })

    unmapped_ids = set(master_hotels.keys()) - seen_master_ids
    counts["unmapped_master_properties"] = len(unmapped_ids)

    return {
        "path": path,
        "aem_pages_audited": len(authored_pages),
        "master_properties_count": len(master_hotels),
        "total_discrepancies": len(discrepancies),
        "summary": counts,
        "discrepancies": discrepancies[:limit]
    }


def audit_localization_coverage(
    all_nodes: dict[str, Any],
    base_locale: str = "us/en",
    target_locales: list[str] | None = None,
    subpath: str = "hotels",
    limit: int = 50,
    ignore_legacy: bool = True
) -> dict[str, Any]:
    """
    Audit multi-region localization coverage comparing canonical base locale to target locales.
    """
    if target_locales is None:
        target_locales = ["gb/en", "fr/fr", "de/de", "es/es", "jp/ja"]

    base_root = f"/content/novaria/{base_locale}/{subpath}"
    canonical_slugs = []

    for path, node in all_nodes.items():
        if path.startswith(base_root + "/") and node.get("jcr:primaryType") == "cq:Page":
            # Extract immediate slug
            rel = path[len(base_root) + 1:]
            if "/" not in rel:
                if ignore_legacy and rel.startswith("legacy-hotel-archive-"):
                    continue
                canonical_slugs.append(rel)

    canonical_slugs = sorted(canonical_slugs)
    total_canonical = len(canonical_slugs)

    results = {}
    for target in target_locales:
        target_root = f"/content/novaria/{target}/{subpath}"
        existing = []
        missing = []

        for slug in canonical_slugs:
            t_path = f"{target_root}/{slug}"
            if t_path in all_nodes and all_nodes[t_path].get("jcr:primaryType") == "cq:Page":
                existing.append(slug)
            else:
                missing.append(slug)

        coverage_pct = round((len(existing) / total_canonical * 100), 2) if total_canonical else 0.0
        results[target] = {
            "canonical_target": total_canonical,
            "existing_pages": len(existing),
            "missing_count": len(missing),
            "coverage_pct": coverage_pct,
            "sample_missing": [f"{target_root}/{s}" for s in missing[:limit]]
        }

    return {
        "base_locale": base_locale,
        "subpath": subpath,
        "canonical_pages": total_canonical,
        "locales": results
    }
