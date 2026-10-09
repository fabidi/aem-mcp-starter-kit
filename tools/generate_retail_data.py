#!/usr/bin/env python3
"""
Retail Domain Seed Generator for AEM Content Intelligence MCP.
Creates:
1. domains/retail/registry.json - Canonical PIM product catalog.
2. domains/retail/jcr_mock_store.json - JCR mock content hierarchy with intentional audit gaps.

Fictional domain: Lumina Lifestyle Retail (luminafashion.example.com)
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RETAIL_DIR = ROOT / "domains" / "retail"
RETAIL_DIR.mkdir(parents=True, exist_ok=True)

PRODUCTS = [
    {"sku": "LUM-JKT-01", "title": "Lumina Alpine Gore-Tex Shell", "category": "Outerwear", "price": "450", "in_stock": "true", "material": "Recycled Polyamide", "status": "ACTIVE"},
    {"sku": "LUM-JKT-02", "title": "Lumina Merino Wool Overcoat", "category": "Outerwear", "price": "320", "in_stock": "true", "material": "100% Merino Wool", "status": "ACTIVE"},
    {"sku": "LUM-FLC-03", "title": "Lumina Glacier Grid Fleece", "category": "Midlayer", "price": "140", "in_stock": "true", "material": "Polartec Thermal", "status": "ACTIVE"},
    {"sku": "LUM-PNT-04", "title": "Lumina Traverse Expedition Pant", "category": "Apparel", "price": "190", "in_stock": "true", "material": "Cordura Ripstop", "status": "ACTIVE"},
    {"sku": "LUM-BOOT-05", "title": "Lumina Summit Waterproof Hiker", "category": "Footwear", "price": "260", "in_stock": "false", "material": "Nubuck Leather", "status": "ACTIVE"},
    {"sku": "LUM-BOOT-06", "title": "Lumina Urban Chelsea Boot", "category": "Footwear", "price": "210", "in_stock": "true", "material": "Full-Grain Leather", "status": "ACTIVE"},
    {"sku": "LUM-BAG-07", "title": "Lumina Nomad 35L Roll-Top Pack", "category": "Equipment", "price": "175", "in_stock": "true", "material": "Ballistic Nylon", "status": "ACTIVE"},
    {"sku": "LUM-BAG-08", "title": "Lumina Minimalist Weekender Duffel", "category": "Equipment", "price": "195", "in_stock": "true", "material": "Weatherproof Canvas", "status": "ACTIVE"},
    {"sku": "LUM-ACC-09", "title": "Lumina Cashmere Beanie", "category": "Accessories", "price": "65", "in_stock": "true", "material": "Mongolian Cashmere", "status": "ACTIVE"},
    {"sku": "LUM-ACC-10", "title": "Lumina Polarized Trail Sunglasses", "category": "Accessories", "price": "120", "in_stock": "true", "material": "Bio-Acetate", "status": "ACTIVE"},
    {"sku": "LUM-SLP-11", "title": "Lumina Zero-Down 800 Sleeping Bag", "category": "Equipment", "price": "380", "in_stock": "true", "material": "800FP Hydrophobic Down", "status": "ACTIVE"},
    {"sku": "LUM-ARC-12", "title": "Lumina Heritage Trench (Archived)", "category": "Outerwear", "price": "550", "in_stock": "false", "material": "Organic Cotton Gabardine", "status": "ARCHIVED"},
]

def generate_json():
    json_path = RETAIL_DIR / "registry.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({"products": PRODUCTS}, f, indent=2)
    print(f"Generated {json_path} with {len(PRODUCTS)} product records.")

def generate_jcr():
    jcr = {}
    jcr["/content"] = {"jcr:primaryType": "sling:Folder"}
    jcr["/content/lumina"] = {
        "jcr:primaryType": "cq:Page",
        "jcr:content": {
            "jcr:primaryType": "cq:PageContent",
            "jcr:title": "Lumina Lifestyle Retail Global Catalog",
            "sling:resourceType": "lumina/components/structure/storefront"
        }
    }
    jcr["/content/lumina/us"] = {"jcr:primaryType": "sling:Folder"}
    jcr["/content/lumina/us/en"] = {"jcr:primaryType": "cq:Page"}
    jcr["/content/lumina/us/en/products"] = {"jcr:primaryType": "cq:Page"}

    # DAM Assets
    jcr["/content/dam"] = {"jcr:primaryType": "sling:Folder"}
    jcr["/content/dam/lumina"] = {"jcr:primaryType": "sling:Folder"}
    jcr["/content/dam/lumina/products"] = {"jcr:primaryType": "sling:Folder"}

    for p in PRODUCTS:
        slug = p["title"].lower().replace(" ", "-").replace("(", "").replace(")", "")
        dam_path = f"/content/dam/lumina/products/{slug}-thumb.jpg"
        jcr[dam_path] = {
            "jcr:primaryType": "dam:Asset",
            "jcr:content": {
                "jcr:primaryType": "dam:AssetContent",
                "metadata": {"dc:title": f"{p['title']} Thumbnail", "dc:format": "image/jpeg"}
            }
        }

    # Populate Product Pages with intentional audit gaps:
    for p in PRODUCTS:
        slug = p["title"].lower().replace(" ", "-").replace("(", "").replace(")", "")
        page_path = f"/content/lumina/us/en/products/{slug}"
        authored_price = p["price"]
        authored_stock = p["in_stock"]
        thumb_ref = f"/content/dam/lumina/products/{slug}-thumb.jpg"

        # Gap 1: Injected Price mismatch on LUM-JKT-02 (author priced at $280 vs PIM $320)
        if p["sku"] == "LUM-JKT-02":
            authored_price = "280"

        # Gap 2: Injected Out-of-Stock mismatch on LUM-BOOT-05 (PIM false, AEM true)
        if p["sku"] == "LUM-BOOT-05":
            authored_stock = "true"

        # Gap 3: Broken DAM thumbnail reference on LUM-BAG-08
        if p["sku"] == "LUM-BAG-08":
            thumb_ref = "/content/dam/lumina/products/broken-weekender-thumb.jpg"

        jcr[page_path] = {
            "jcr:primaryType": "cq:Page",
            "jcr:content": {
                "jcr:primaryType": "cq:PageContent",
                "jcr:title": p["title"],
                "sling:resourceType": "lumina/components/structure/product-page",
                "sku": p["sku"],
                "category": p["category"],
                "price": authored_price,
                "inStock": authored_stock,
                "material": p["material"],
                "status": p["status"],
                "thumbnail": {"fileReference": thumb_ref}
            }
        }

    jcr_path = RETAIL_DIR / "jcr_mock_store.json"
    with open(jcr_path, "w", encoding="utf-8") as f:
        json.dump(jcr, f, indent=2)
    print(f"Generated {jcr_path} with {len(jcr)} JCR nodes.")

if __name__ == "__main__":
    generate_json()
    generate_jcr()
