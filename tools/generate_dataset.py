#!/usr/bin/env python3
"""
Synthetic Dataset Generator for AEM Content Intelligence MCP Starter Kit.

Generates:
1. data/property_master.sqlite - Canonical Master Entity Registry (120 hotels, 4 brands, 35 cities).
2. data/jcr_mock_store.json     - Hierarchical simulated JCR repository (Pages, CFs, XFs, DAM).
3. Injected audit discrepancies (stale ratings, missing translations, broken references, expired promos).

Clean 'Meridian Hospitality Group' domain.
"""

import json
import sqlite3
from pathlib import Path
from datetime import datetime, timezone
import random

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"

# 4 Canonical Brands
BRANDS = [
    {
        "brand_id": "meridian-grand",
        "brand_name": "Meridian Grand",
        "tier": "Luxury",
        "description": "Flagship grand hotels in premier global capitals offering timeless elegance and bespoke service.",
        "standards": {
            "min_star_rating": 5,
            "concierge_24h": True,
            "michelin_dining_required": True,
            "spa_required": True,
            "checkin_time": "15:00",
            "checkout_time": "12:00"
        }
    },
    {
        "brand_id": "meridian-house",
        "brand_name": "Meridian House",
        "tier": "Boutique",
        "description": "Design-centric urban lifestyle hotels embedded in historic and cultural arts districts.",
        "standards": {
            "min_star_rating": 4,
            "concierge_24h": True,
            "rooftop_lounge_required": True,
            "checkin_time": "15:00",
            "checkout_time": "11:00"
        }
    },
    {
        "brand_id": "solstice-resorts",
        "brand_name": "Solstice Resorts",
        "tier": "Resort",
        "description": "World-class beachfront and alpine retreats focused on wellness, nature, and rejuvenation.",
        "standards": {
            "min_star_rating": 5,
            "private_villas_available": True,
            "wellness_retreat_required": True,
            "checkin_time": "16:00",
            "checkout_time": "11:00"
        }
    },
    {
        "brand_id": "meridian-select",
        "brand_name": "Meridian Select",
        "tier": "Premium",
        "description": "Efficient, high-tech hospitality for modern business travelers in global financial centers.",
        "standards": {
            "min_star_rating": 4,
            "high_speed_fiber": True,
            "executive_meeting_rooms": True,
            "checkin_time": "14:00",
            "checkout_time": "12:00"
        }
    }
]

# 35 Global Destinations
DESTINATIONS = [
    {"code": "NYC", "city": "New York", "country": "United States", "country_code": "US", "region": "Americas", "lat": 40.7128, "lng": -74.0060},
    {"code": "MIA", "city": "Miami", "country": "United States", "country_code": "US", "region": "Americas", "lat": 25.7617, "lng": -80.1918},
    {"code": "SFO", "city": "San Francisco", "country": "United States", "country_code": "US", "region": "Americas", "lat": 37.7749, "lng": -122.4194},
    {"code": "CHI", "city": "Chicago", "country": "United States", "country_code": "US", "region": "Americas", "lat": 41.8781, "lng": -87.6298},
    {"code": "LAX", "city": "Los Angeles", "country": "United States", "country_code": "US", "region": "Americas", "lat": 34.0522, "lng": -118.2437},
    {"code": "YYZ", "city": "Toronto", "country": "Canada", "country_code": "CA", "region": "Americas", "lat": 43.6532, "lng": -79.3832},
    {"code": "MEX", "city": "Mexico City", "country": "Mexico", "country_code": "MX", "region": "Americas", "lat": 19.4326, "lng": -99.1332},
    {"code": "GRU", "city": "São Paulo", "country": "Brazil", "country_code": "BR", "region": "Americas", "lat": -23.5505, "lng": -46.6333},
    {"code": "LON", "city": "London", "country": "United Kingdom", "country_code": "GB", "region": "EMEA", "lat": 51.5074, "lng": -0.1278},
    {"code": "PAR", "city": "Paris", "country": "France", "country_code": "FR", "region": "EMEA", "lat": 48.8566, "lng": 2.3522},
    {"code": "BER", "city": "Berlin", "country": "Germany", "country_code": "DE", "region": "EMEA", "lat": 52.5200, "lng": 13.4050},
    {"code": "AMS", "city": "Amsterdam", "country": "Netherlands", "country_code": "NL", "region": "EMEA", "lat": 52.3676, "lng": 4.9041},
    {"code": "FCO", "city": "Rome", "country": "Italy", "country_code": "IT", "region": "EMEA", "lat": 41.9028, "lng": 12.4964},
    {"code": "MAD", "city": "Madrid", "country": "Spain", "country_code": "ES", "region": "EMEA", "lat": 40.4168, "lng": -3.7038},
    {"code": "BCN", "city": "Barcelona", "country": "Spain", "country_code": "ES", "region": "EMEA", "lat": 41.3851, "lng": 2.1734},
    {"code": "ZRH", "city": "Zurich", "country": "Switzerland", "country_code": "CH", "region": "EMEA", "lat": 47.3769, "lng": 8.5417},
    {"code": "VIE", "city": "Vienna", "country": "Austria", "country_code": "AT", "region": "EMEA", "lat": 48.2082, "lng": 16.3738},
    {"code": "DUB", "city": "Dublin", "country": "Ireland", "country_code": "IE", "region": "EMEA", "lat": 53.3498, "lng": -6.2603},
    {"code": "DXB", "city": "Dubai", "country": "United Arab Emirates", "country_code": "AE", "region": "EMEA", "lat": 25.2048, "lng": 55.2708},
    {"code": "DOH", "city": "Doha", "country": "Qatar", "country_code": "QA", "region": "EMEA", "lat": 25.2854, "lng": 51.5310},
    {"code": "CPT", "city": "Cape Town", "country": "South Africa", "country_code": "ZA", "region": "EMEA", "lat": -33.9249, "lng": 18.4241},
    {"code": "TYO", "city": "Tokyo", "country": "Japan", "country_code": "JP", "region": "APAC", "lat": 35.6762, "lng": 139.6503},
    {"code": "KIX", "city": "Kyoto", "country": "Japan", "country_code": "JP", "region": "APAC", "lat": 35.0116, "lng": 135.7681},
    {"code": "SIN", "city": "Singapore", "country": "Singapore", "country_code": "SG", "region": "APAC", "lat": 1.3521, "lng": 103.8198},
    {"code": "HKG", "city": "Hong Kong", "country": "Hong Kong", "country_code": "HK", "region": "APAC", "lat": 22.3193, "lng": 114.1694},
    {"code": "BKK", "city": "Bangkok", "country": "Thailand", "country_code": "TH", "region": "APAC", "lat": 13.7563, "lng": 100.5018},
    {"code": "SYD", "city": "Sydney", "country": "Australia", "country_code": "AU", "region": "APAC", "lat": -33.8688, "lng": 151.2093},
    {"code": "MEL", "city": "Melbourne", "country": "Australia", "country_code": "AU", "region": "APAC", "lat": -37.8136, "lng": 144.9631},
    {"code": "AKL", "city": "Auckland", "country": "New Zealand", "country_code": "NZ", "region": "APAC", "lat": -36.8485, "lng": 174.7633},
    {"code": "ICN", "city": "Seoul", "country": "South Korea", "country_code": "KR", "region": "APAC", "lat": 37.5665, "lng": 126.9780},
    {"code": "DPS", "city": "Bali", "country": "Indonesia", "country_code": "ID", "region": "APAC", "lat": -8.4095, "lng": 115.1889},
    {"code": "MLE", "city": "Malé", "country": "Maldives", "country_code": "MV", "region": "APAC", "lat": 4.1755, "lng": 73.5093},
    {"code": "BOM", "city": "Mumbai", "country": "India", "country_code": "IN", "region": "APAC", "lat": 19.0760, "lng": 72.8777},
    {"code": "DEL", "city": "New Delhi", "country": "India", "country_code": "IN", "region": "APAC", "lat": 28.6139, "lng": 77.2090},
    {"code": "HNL", "city": "Honolulu", "country": "United States", "country_code": "US", "region": "Americas", "lat": 21.3069, "lng": -157.8583}
]

ALL_AMENITIES = [
    "Spa & Hydrotherapy", "Rooftop Infinity Pool", "Michelin-starred Dining",
    "Executive Club Lounge", "Private Beach Access", "24/7 Butler Service",
    "Chauffeur Valet", "State-of-the-Art Technogym", "Wine Tasting Cellar",
    "High-speed Quantum Fiber", "Helipad Access", "Pet Concierge",
    "Artisanal Coffee Roastery", "EV Charging Stations", "Yacht Charter Service"
]

def generate_hotels():
    random.seed(42)
    hotels = []
    hotel_num = 1

    # Distribute 120 hotels across 35 destinations
    for dest in DESTINATIONS:
        # 3 to 4 hotels per destination
        count = 4 if dest["code"] in {"NYC", "LON", "PAR", "TYO", "DXB"} else 3
        for i in range(count):
            if hotel_num > 120:
                break
            
            # Select brand based on location vibe
            if dest["code"] in {"DPS", "MLE", "HNL"}:
                brand = next(b for b in BRANDS if b["brand_id"] == "solstice-resorts")
            elif i == 0:
                brand = next(b for b in BRANDS if b["brand_id"] == "meridian-grand")
            elif i == 1:
                brand = next(b for b in BRANDS if b["brand_id"] == "meridian-house")
            else:
                brand = next(b for b in BRANDS if b["brand_id"] == "meridian-select")
            
            hotel_id = f"MDN-{dest['code']}-{hotel_num:03d}"
            
            # Name formatting
            if brand["brand_id"] == "meridian-grand":
                name = f"The Meridian Grand {dest['city']}"
                slug = f"{dest['city'].lower().replace(' ', '-')}-grand"
            elif brand["brand_id"] == "meridian-house":
                descriptors = ["SoHo", "Mayfair", "Le Marais", "Ginza", "Downtown", "Harbour", "Quartier"]
                sub = descriptors[(hotel_num + i) % len(descriptors)]
                name = f"Meridian House {dest['city']} {sub}"
                slug = f"{dest['city'].lower().replace(' ', '-')}-house-{sub.lower()}"
            elif brand["brand_id"] == "solstice-resorts":
                name = f"Solstice Resort & Wellness {dest['city']}"
                slug = f"{dest['city'].lower().replace(' ', '-')}-solstice-resort"
            else:
                name = f"Meridian Select {dest['city']} Central"
                slug = f"{dest['city'].lower().replace(' ', '-')}-select"
            
            star_rating = brand["standards"]["min_star_rating"]
            if brand["brand_id"] in {"meridian-house", "meridian-select"} and random.random() > 0.4:
                star_rating = 5

            amenity_sample = random.sample(ALL_AMENITIES, k=random.randint(6, 9))
            
            hotels.append({
                "hotel_id": hotel_id,
                "brand_id": brand["brand_id"],
                "name": name,
                "slug": slug,
                "destination_code": dest["code"],
                "city": dest["city"],
                "country": dest["country"],
                "country_code": dest["country_code"],
                "region": dest["region"],
                "latitude": round(dest["lat"] + random.uniform(-0.04, 0.04), 4),
                "longitude": round(dest["lng"] + random.uniform(-0.04, 0.04), 4),
                "star_rating": star_rating,
                "total_rooms": random.randint(110, 480),
                "status": "Active" if hotel_num <= 115 else "Renovating",
                "phone": f"+{random.randint(1, 99)} {random.randint(100, 999)} {random.randint(1000, 9999)}",
                "email": f"concierge.{slug}@meridianhotels.com",
                "amenities": amenity_sample,
                "updated_at": datetime.now(timezone.utc).isoformat()
            })
            hotel_num += 1

    return hotels

def build_database(hotels):
    DATA_DIR.mkdir(exist_ok=True, parents=True)
    db_path = DATA_DIR / "property_master.sqlite"
    if db_path.exists():
        db_path.unlink()

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE brands (
        brand_id TEXT PRIMARY KEY,
        brand_name TEXT NOT NULL,
        tier TEXT NOT NULL,
        description TEXT,
        standards JSON NOT NULL
    );
    """)

    cur.execute("""
    CREATE TABLE destinations (
        code TEXT PRIMARY KEY,
        city TEXT NOT NULL,
        country TEXT NOT NULL,
        country_code TEXT NOT NULL,
        region TEXT NOT NULL,
        latitude REAL,
        longitude REAL
    );
    """)

    cur.execute("""
    CREATE TABLE properties (
        hotel_id TEXT PRIMARY KEY,
        brand_id TEXT NOT NULL,
        name TEXT NOT NULL,
        slug TEXT NOT NULL,
        destination_code TEXT NOT NULL,
        city TEXT NOT NULL,
        country TEXT NOT NULL,
        country_code TEXT NOT NULL,
        region TEXT NOT NULL,
        latitude REAL NOT NULL,
        longitude REAL NOT NULL,
        star_rating INTEGER NOT NULL,
        total_rooms INTEGER NOT NULL,
        status TEXT NOT NULL,
        phone TEXT,
        email TEXT,
        amenities JSON NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY (brand_id) REFERENCES brands(brand_id),
        FOREIGN KEY (destination_code) REFERENCES destinations(code)
    );
    """)

    for b in BRANDS:
        cur.execute("INSERT INTO brands VALUES (?, ?, ?, ?, ?)",
                    (b["brand_id"], b["brand_name"], b["tier"], b["description"], json.dumps(b["standards"])))

    for d in DESTINATIONS:
        cur.execute("INSERT INTO destinations VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (d["code"], d["city"], d["country"], d["country_code"], d["region"], d["lat"], d["lng"]))

    for h in hotels:
        cur.execute("""
        INSERT INTO properties VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            h["hotel_id"], h["brand_id"], h["name"], h["slug"], h["destination_code"],
            h["city"], h["country"], h["country_code"], h["region"], h["latitude"],
            h["longitude"], h["star_rating"], h["total_rooms"], h["status"],
            h["phone"], h["email"], json.dumps(h["amenities"]), h["updated_at"]
        ))

    conn.commit()
    conn.close()
    print(f"Created Property Master DB at {db_path} with {len(hotels)} hotels.")

def build_jcr_tree(hotels):
    """
    Constructs a rich simulated JCR repository with deliberate audit anomalies:
    - 6 Stale star ratings in AEM page content
    - 14 Missing French or German translations
    - 8 Broken DAM asset references
    - 12 Expired promo campaign Experience Fragment references
    - 3 Orphaned AEM pages with invalid/unknown hotelId
    """
    jcr = {}

    # Root nodes
    jcr["/content"] = {
        "jcr:primaryType": "sling:Folder",
        "jcr:title": "Content Root"
    }
    jcr["/content/meridian"] = {
        "jcr:primaryType": "cq:Page",
        "jcr:content": {
            "jcr:primaryType": "cq:PageContent",
            "jcr:title": "Meridian Hotels & Resorts Global Site",
            "sling:resourceType": "meridian/components/structure/homepage"
        }
    }
    
    # Common DAM asset folders
    jcr["/content/dam"] = {"jcr:primaryType": "sling:Folder"}
    jcr["/content/dam/meridian"] = {"jcr:primaryType": "sling:Folder"}
    jcr["/content/dam/meridian/content-fragments"] = {"jcr:primaryType": "sling:Folder"}
    jcr["/content/dam/meridian/content-fragments/properties"] = {"jcr:primaryType": "sling:Folder"}
    jcr["/content/dam/meridian/hotels"] = {"jcr:primaryType": "sling:Folder"}

    # Common Experience Fragments
    jcr["/content/experience-fragments"] = {"jcr:primaryType": "sling:Folder"}
    jcr["/content/experience-fragments/meridian"] = {"jcr:primaryType": "sling:Folder"}
    jcr["/content/experience-fragments/meridian/us/en/promos"] = {"jcr:primaryType": "sling:Folder"}
    
    # Promo 1: Active 2026 Summer Promo
    jcr["/content/experience-fragments/meridian/us/en/promos/summer-getaway-2026"] = {
        "jcr:primaryType": "cq:Page",
        "jcr:content": {
            "jcr:primaryType": "cq:PageContent",
            "jcr:title": "Summer Getaway 2026 Promo Banner",
            "sling:resourceType": "meridian/components/xfpage",
            "campaignStatus": "active",
            "discountCode": "SUMMER26",
            "validThrough": "2026-09-30"
        }
    }

    # Promo 2: Expired 2024 Winter Promo (Audit target!)
    jcr["/content/experience-fragments/meridian/us/en/promos/winter-escape-2024"] = {
        "jcr:primaryType": "cq:Page",
        "jcr:content": {
            "jcr:primaryType": "cq:PageContent",
            "jcr:title": "Winter Escape 2024 Campaign (EXPIRED)",
            "sling:resourceType": "meridian/components/xfpage",
            "campaignStatus": "expired",
            "discountCode": "WINTER24",
            "validThrough": "2024-03-31"
        }
    }

    # Models & Templates
    jcr["/conf/meridian/settings/wcm/templates/hotel-page"] = {
        "jcr:primaryType": "cq:Template",
        "jcr:title": "Hotel Detail Page Template",
        "allowedPaths": ["/content/meridian(/.*)?"]
    }
    jcr["/conf/meridian/settings/dam/cfm/models/hotel-fragment"] = {
        "jcr:primaryType": "dam:Asset",
        "jcr:title": "Hotel Content Fragment Model"
    }

    # Languages supported
    languages = [
        ("us", "en", "English (US)"),
        ("gb", "en", "English (UK)"),
        ("fr", "fr", "Français"),
        ("de", "de", "Deutsch"),
        ("jp", "ja", "日本語"),
        ("es", "es", "Español")
    ]

    for country_folder, lang, title in languages:
        root_path = f"/content/meridian/{country_folder}/{lang}"
        jcr[root_path] = {
            "jcr:primaryType": "cq:Page",
            "jcr:content": {
                "jcr:primaryType": "cq:PageContent",
                "jcr:title": title,
                "sling:resourceType": "meridian/components/structure/langroot"
            }
        }
        jcr[f"{root_path}/hotels"] = {
            "jcr:primaryType": "cq:Page",
            "jcr:content": {
                "jcr:primaryType": "cq:PageContent",
                "jcr:title": f"Explore Hotels ({title})",
                "sling:resourceType": "meridian/components/structure/hotellist"
            }
        }

    # Anomaly tracking
    stale_rating_hotels = set([h["hotel_id"] for h in hotels[10:16]]) # 6 hotels
    broken_dam_hotels = set([h["hotel_id"] for h in hotels[25:33]])   # 8 hotels
    expired_promo_hotels = set([h["hotel_id"] for h in hotels[40:52]]) # 12 hotels
    missing_french_hotels = set([h["hotel_id"] for h in hotels[60:74] if h["region"] == "EMEA"]) # ~10-14 hotels

    for h in hotels:
        slug = h["slug"]
        cf_path = f"/content/dam/meridian/content-fragments/properties/{slug}"
        
        # 1. Content Fragment in DAM
        jcr[cf_path] = {
            "jcr:primaryType": "dam:Asset",
            "jcr:content": {
                "jcr:primaryType": "dam:AssetContent",
                "contentFragment": True,
                "cq:model": "/conf/meridian/settings/dam/cfm/models/hotel-fragment",
                "data": {
                    "master": {
                        "hotelId": h["hotel_id"],
                        "brandId": h["brand_id"],
                        "headline": f"Experience luxury at {h['name']}",
                        "overview": f"Situated in the vibrant heart of {h['city']}, {h['name']} offers an extraordinary refuge featuring {h['amenities'][0].lower()} and {h['amenities'][1].lower()}.",
                        "roomInventory": h["total_rooms"],
                        "amenities": h["amenities"],
                        "primaryPhone": h["phone"],
                        "contactEmail": h["email"]
                    }
                }
            }
        }

        # 2. DAM Hero Image
        hero_dam_path = f"/content/dam/meridian/hotels/{slug}/hero.jpg"
        if h["hotel_id"] not in broken_dam_hotels:
            jcr[hero_dam_path] = {
                "jcr:primaryType": "dam:Asset",
                "jcr:content": {
                    "jcr:primaryType": "dam:AssetContent",
                    "metadata": {
                        "dc:title": f"{h['name']} Exterior Hero",
                        "dc:format": "image/jpeg",
                        "dam:width": 3840,
                        "dam:height": 2160
                    }
                }
            }

        # 3. Authored Page in US/EN
        page_path = f"/content/meridian/us/en/hotels/{slug}"
        
        # Injected Discrepancy 1: Stale Star Rating
        authored_rating = 4 if h["hotel_id"] in stale_rating_hotels else h["star_rating"]

        # Injected Discrepancy 4: Expired Promo Fragment
        promo_ref = (
            "/content/experience-fragments/meridian/us/en/promos/winter-escape-2024"
            if h["hotel_id"] in expired_promo_hotels
            else "/content/experience-fragments/meridian/us/en/promos/summer-getaway-2026"
        )

        jcr[page_path] = {
            "jcr:primaryType": "cq:Page",
            "jcr:content": {
                "jcr:primaryType": "cq:PageContent",
                "jcr:title": h["name"],
                "jcr:description": f"Official page for {h['name']} in {h['city']}.",
                "cq:template": "/conf/meridian/settings/wcm/templates/hotel-page",
                "sling:resourceType": "meridian/components/structure/page",
                "cq:tags": [f"meridian:brands/{h['brand_id']}", f"meridian:destinations/{h['destination_code']}"],
                "hotelId": h["hotel_id"],
                "brandId": h["brand_id"],
                "authoredStarRating": authored_rating,
                "root": {
                    "jcr:primaryType": "nt:unstructured",
                    "sling:resourceType": "meridian/components/container",
                    "hero": {
                        "jcr:primaryType": "nt:unstructured",
                        "sling:resourceType": "meridian/components/content/hero",
                        "fileReference": hero_dam_path,
                        "title": h["name"]
                    },
                    "cf_component": {
                        "jcr:primaryType": "nt:unstructured",
                        "sling:resourceType": "meridian/components/content/contentfragment",
                        "fragmentPath": cf_path
                    },
                    "promo_banner": {
                        "jcr:primaryType": "nt:unstructured",
                        "sling:resourceType": "meridian/components/content/experiencefragment",
                        "fragmentPath": promo_ref
                    }
                }
            }
        }

        # 4. Localized Copies (e.g. French /fr/fr/)
        if h["hotel_id"] not in missing_french_hotels:
            fr_page_path = f"/content/meridian/fr/fr/hotels/{slug}"
            jcr[fr_page_path] = {
                "jcr:primaryType": "cq:Page",
                "jcr:content": {
                    "jcr:primaryType": "cq:PageContent",
                    "jcr:title": f"{h['name']} - Séjour d'Exception",
                    "cq:template": "/conf/meridian/settings/wcm/templates/hotel-page",
                    "sling:resourceType": "meridian/components/structure/page",
                    "hotelId": h["hotel_id"],
                    "brandId": h["brand_id"],
                    "authoredStarRating": authored_rating,
                    "root": {
                        "hero": {"fileReference": hero_dam_path},
                        "cf_component": {"fragmentPath": cf_path}
                    }
                }
            }

    # Injected Discrepancy 5: Orphaned / Unregistered Legacy AEM Pages (hotelId not in Registry)
    orphans = [
        {"slug": "legacy-grand-chicago", "name": "Meridian Grand Chicago (Demolished)", "bad_id": "MDN-CHI-999"},
        {"slug": "legacy-solstice-tahoe", "name": "Solstice Lake Tahoe (Sold)", "bad_id": "MDN-RNO-888"},
        {"slug": "unmapped-berlin-lodge", "name": "Meridian Lodge Berlin (Unregistered)", "bad_id": "MDN-BER-777"}
    ]
    for o in orphans:
        p = f"/content/meridian/us/en/hotels/{o['slug']}"
        jcr[p] = {
            "jcr:primaryType": "cq:Page",
            "jcr:content": {
                "jcr:primaryType": "cq:PageContent",
                "jcr:title": o["name"],
                "cq:template": "/conf/meridian/settings/wcm/templates/hotel-page",
                "sling:resourceType": "meridian/components/structure/page",
                "hotelId": o["bad_id"],
                "brandId": "meridian-grand",
                "root": {
                    "hero": {"fileReference": "/content/dam/meridian/hotels/legacy/hero.jpg"}
                }
            }
        }

    # Save to JSON store
    json_path = DATA_DIR / "jcr_mock_store.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(jcr, f, indent=2, ensure_ascii=False)

    print(f"Created Synthetic JCR Tree at {json_path} with {len(jcr)} nodes.")
    print("Injected Audit Discrepancies Summary:")
    print(f"  - Stale Star Ratings: {len(stale_rating_hotels)} properties")
    print(f"  - Broken DAM Hero Assets: {len(broken_dam_hotels)} properties")
    print(f"  - Expired Promo Fragments: {len(expired_promo_hotels)} properties")
    print(f"  - Missing French Translations: {len(missing_french_hotels)} properties")
    print(f"  - Orphaned Unregistered Pages: {len(orphans)} pages")

def main():
    print("Generating synthetic datasets for AEM Content Intelligence Starter Kit...")
    hotels = generate_hotels()
    build_database(hotels)
    build_jcr_tree(hotels)
    print("Dataset generation complete!")

if __name__ == "__main__":
    main()
