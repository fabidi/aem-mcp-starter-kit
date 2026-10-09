#!/usr/bin/env python3
"""
Automotive Domain Seed Generator for AEM Content Intelligence MCP.
Creates:
1. domains/automotive/registry.csv - Canonical ERP / PIM vehicle catalog.
2. domains/automotive/jcr_mock_store.json - JCR mock content hierarchy with intentional audit gaps.

Fictional domain: Apex Motor Works (apexmotors.example.com)
"""

import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AUTO_DIR = ROOT / "domains" / "automotive"
AUTO_DIR.mkdir(parents=True, exist_ok=True)

VEHICLES = [
    {"vin_prefix": "APX-SED-01", "model_code": "SOL-26", "name": "Apex Solace Sedan", "brand_tier": "Core", "category": "Sedan", "base_msrp": "38500", "powertrain": "2.0L Turbo I4", "horsepower": "250", "fuel_economy": "32 mpg", "status": "ACTIVE", "featured_trim": "Aero"},
    {"vin_prefix": "APX-SED-02", "model_code": "SOL-PREM", "name": "Apex Solace Executive", "brand_tier": "Luxury", "category": "Sedan", "base_msrp": "46000", "powertrain": "2.5L Turbo I4", "horsepower": "300", "fuel_economy": "28 mpg", "status": "ACTIVE", "featured_trim": "Executive"},
    {"vin_prefix": "APX-EV-03", "model_code": "PUL-EV", "name": "Apex Pulse EV", "brand_tier": "Electric", "category": "Electric", "base_msrp": "54000", "powertrain": "Dual Motor AWD", "horsepower": "420", "fuel_economy": "112 mpge", "status": "ACTIVE", "featured_trim": "Long Range"},
    {"vin_prefix": "APX-EV-04", "model_code": "PUL-GT", "name": "Apex Pulse Performance EV", "brand_tier": "Electric", "category": "Electric", "base_msrp": "68500", "powertrain": "Tri-Motor AWD", "horsepower": "580", "fuel_economy": "98 mpge", "status": "ACTIVE", "featured_trim": "GT Track"},
    {"vin_prefix": "APX-SUV-05", "model_code": "HOR-26", "name": "Apex Horizon Crossover", "brand_tier": "Core", "category": "SUV", "base_msrp": "41200", "powertrain": "2.4L Turbo I4", "horsepower": "265", "fuel_economy": "27 mpg", "status": "ACTIVE", "featured_trim": "Adventure"},
    {"vin_prefix": "APX-SUV-06", "model_code": "HOR-MAX", "name": "Apex Horizon Grand 7-Seat", "brand_tier": "Core", "category": "SUV", "base_msrp": "48900", "powertrain": "3.0L Turbo V6", "horsepower": "355", "fuel_economy": "24 mpg", "status": "ACTIVE", "featured_trim": "Touring"},
    {"vin_prefix": "APX-SUV-07", "model_code": "PIN-LUX", "name": "Apex Pinnacle Luxury SUV", "brand_tier": "Luxury", "category": "SUV", "base_msrp": "79500", "powertrain": "3.5L Twin-Turbo V6", "horsepower": "450", "fuel_economy": "21 mpg", "status": "ACTIVE", "featured_trim": "Bespoke"},
    {"vin_prefix": "APX-SPT-08", "model_code": "VNG-GT", "name": "Apex Vanguard Coupe", "brand_tier": "Performance", "category": "Sports", "base_msrp": "62000", "powertrain": "3.0L Twin-Turbo I6", "horsepower": "405", "fuel_economy": "23 mpg", "status": "ACTIVE", "featured_trim": "Sprint"},
    {"vin_prefix": "APX-SPT-09", "model_code": "VNG-R", "name": "Apex Vanguard Track Edition", "brand_tier": "Performance", "category": "Sports", "base_msrp": "78000", "powertrain": "3.0L Twin-Turbo I6", "horsepower": "480", "fuel_economy": "20 mpg", "status": "ACTIVE", "featured_trim": "Carbon Track"},
    {"vin_prefix": "APX-TRM-10", "model_code": "ZEN-DISC", "name": "Apex Zenith Heritage Coupe", "brand_tier": "Heritage", "category": "Sports", "base_msrp": "85000", "powertrain": "4.0L V8", "horsepower": "500", "fuel_economy": "17 mpg", "status": "DISCONTINUED", "featured_trim": "Collector"},
    {"vin_prefix": "APX-HYB-11", "model_code": "AUR-PHEV", "name": "Apex Aurora Hybrid Wagon", "brand_tier": "Core", "category": "Wagon", "base_msrp": "44500", "powertrain": "2.0L Hybrid PHEV", "horsepower": "290", "fuel_economy": "75 mpge", "status": "ACTIVE", "featured_trim": "All-Road"},
    {"vin_prefix": "APX-TRK-12", "model_code": "TIT-PU", "name": "Apex Titan Heavy Duty", "brand_tier": "Commercial", "category": "Truck", "base_msrp": "52000", "powertrain": "6.7L Turbo Diesel V8", "horsepower": "475", "fuel_economy": "19 mpg", "status": "ACTIVE", "featured_trim": "Workhorse"},
]

def generate_csv():
    csv_path = AUTO_DIR / "registry.csv"
    fields = list(VEHICLES[0].keys())
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(VEHICLES)
    print(f"Generated {csv_path} with {len(VEHICLES)} vehicle records.")

def generate_jcr():
    jcr = {}
    jcr["/content"] = {"jcr:primaryType": "sling:Folder"}
    jcr["/content/apex"] = {
        "jcr:primaryType": "cq:Page",
        "jcr:content": {
            "jcr:primaryType": "cq:PageContent",
            "jcr:title": "Apex Motor Works Official Portal",
            "sling:resourceType": "apex/components/structure/homepage"
        }
    }
    jcr["/content/apex/us"] = {"jcr:primaryType": "sling:Folder"}
    jcr["/content/apex/us/en"] = {"jcr:primaryType": "cq:Page"}
    jcr["/content/apex/us/en/vehicles"] = {"jcr:primaryType": "cq:Page"}

    # Common DAM assets
    jcr["/content/dam"] = {"jcr:primaryType": "sling:Folder"}
    jcr["/content/dam/apex"] = {"jcr:primaryType": "sling:Folder"}
    jcr["/content/dam/apex/vehicles"] = {"jcr:primaryType": "sling:Folder"}

    for v in VEHICLES:
        slug = v["name"].lower().replace(" ", "-")
        dam_path = f"/content/dam/apex/vehicles/{slug}-hero.png"
        jcr[dam_path] = {
            "jcr:primaryType": "dam:Asset",
            "jcr:content": {
                "jcr:primaryType": "dam:AssetContent",
                "metadata": {"dc:title": f"{v['name']} Exterior Hero", "dc:format": "image/png"}
            }
        }

    # Populate Vehicle Pages with intentional audit gaps:
    for v in VEHICLES:
        slug = v["name"].lower().replace(" ", "-")
        page_path = f"/content/apex/us/en/vehicles/{slug}"
        authored_msrp = v["base_msrp"]
        authored_status = v["status"]
        hero_ref = f"/content/dam/apex/vehicles/{slug}-hero.png"

        # Gap 1: Injected MSRP mismatch on APX-SUV-06 (author priced at $45,500 vs ERP $48,900)
        if v["vin_prefix"] == "APX-SUV-06":
            authored_msrp = "45500"

        # Gap 2: Injected Status mismatch on discontinued Zenith (APX-TRM-10) -> AEM still ACTIVE
        if v["vin_prefix"] == "APX-TRM-10":
            authored_status = "ACTIVE"

        # Gap 3: Broken DAM render reference on APX-SPT-09
        if v["vin_prefix"] == "APX-SPT-09":
            hero_ref = "/content/dam/apex/vehicles/non-existent-track-hero.png"

        jcr[page_path] = {
            "jcr:primaryType": "cq:Page",
            "jcr:content": {
                "jcr:primaryType": "cq:PageContent",
                "jcr:title": v["name"],
                "sling:resourceType": "apex/components/structure/vehicle-page",
                "vinPrefix": v["vin_prefix"],
                "modelCode": v["model_code"],
                "category": v["category"],
                "msrp": authored_msrp,
                "powertrain": v["powertrain"],
                "status": authored_status,
                "featuredTrim": v["featured_trim"],
                "heroAsset": {"fileReference": hero_ref}
            }
        }

    jcr_path = AUTO_DIR / "jcr_mock_store.json"
    with open(jcr_path, "w", encoding="utf-8") as f:
        json.dump(jcr, f, indent=2)
    print(f"Generated {jcr_path} with {len(jcr)} JCR nodes.")

if __name__ == "__main__":
    generate_csv()
    generate_jcr()
