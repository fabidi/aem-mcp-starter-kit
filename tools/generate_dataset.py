#!/usr/bin/env python3
"""
Synthetic Dataset Generator for AEM Content Intelligence MCP Starter Kit.

Generates:
1. data/property_master.sqlite - Canonical Master Entity Registry (1,000 hotels, 4 brands, 50 destinations).
2. data/jcr_mock_store.json     - Hierarchical simulated JCR repository (10,000+ nodes: Pages, CFs, XFs, DAM).
3. Injected enterprise audit discrepancies (stale ratings, missing translations, broken references, expired promos).

Unmistakably fictional 'Novaria Hospitality Group' domain (novariahotels.com).
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
        "brand_id": "novaria-grand",
        "brand_name": "Novaria Grand",
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
        "brand_id": "novaria-house",
        "brand_name": "Novaria House",
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
        "brand_id": "novaria-select",
        "brand_name": "Novaria Select",
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

# 50 Global Destinations
DESTINATIONS = [
    {"code": "NYC", "city": "New York", "country": "United States", "country_code": "US", "region": "Americas", "lat": 40.7128, "lng": -74.0060},
    {"code": "MIA", "city": "Miami", "country": "United States", "country_code": "US", "region": "Americas", "lat": 25.7617, "lng": -80.1918},
    {"code": "SFO", "city": "San Francisco", "country": "United States", "country_code": "US", "region": "Americas", "lat": 37.7749, "lng": -122.4194},
    {"code": "CHI", "city": "Chicago", "country": "United States", "country_code": "US", "region": "Americas", "lat": 41.8781, "lng": -87.6298},
    {"code": "LAX", "city": "Los Angeles", "country": "United States", "country_code": "US", "region": "Americas", "lat": 34.0522, "lng": -118.2437},
    {"code": "BOS", "city": "Boston", "country": "United States", "country_code": "US", "region": "Americas", "lat": 42.3601, "lng": -71.0589},
    {"code": "SEA", "city": "Seattle", "country": "United States", "country_code": "US", "region": "Americas", "lat": 47.6062, "lng": -122.3321},
    {"code": "HNL", "city": "Honolulu", "country": "United States", "country_code": "US", "region": "Americas", "lat": 21.3069, "lng": -157.8583},
    {"code": "YYZ", "city": "Toronto", "country": "Canada", "country_code": "CA", "region": "Americas", "lat": 43.6532, "lng": -79.3832},
    {"code": "YVR", "city": "Vancouver", "country": "Canada", "country_code": "CA", "region": "Americas", "lat": 49.2827, "lng": -123.1207},
    {"code": "MEX", "city": "Mexico City", "country": "Mexico", "country_code": "MX", "region": "Americas", "lat": 19.4326, "lng": -99.1332},
    {"code": "GRU", "city": "São Paulo", "country": "Brazil", "country_code": "BR", "region": "Americas", "lat": -23.5505, "lng": -46.6333},
    {"code": "EZE", "city": "Buenos Aires", "country": "Argentina", "country_code": "AR", "region": "Americas", "lat": -34.6037, "lng": -58.3816},
    {"code": "SCL", "city": "Santiago", "country": "Chile", "country_code": "CL", "region": "Americas", "lat": -33.4489, "lng": -70.6693},
    {"code": "BOG", "city": "Bogota", "country": "Colombia", "country_code": "CO", "region": "Americas", "lat": 4.7110, "lng": -74.0721},
    {"code": "LON", "city": "London", "country": "United Kingdom", "country_code": "GB", "region": "EMEA", "lat": 51.5074, "lng": -0.1278},
    {"code": "EDI", "city": "Edinburgh", "country": "United Kingdom", "country_code": "GB", "region": "EMEA", "lat": 55.9533, "lng": -3.1883},
    {"code": "PAR", "city": "Paris", "country": "France", "country_code": "FR", "region": "EMEA", "lat": 48.8566, "lng": 2.3522},
    {"code": "BER", "city": "Berlin", "country": "Germany", "country_code": "DE", "region": "EMEA", "lat": 52.5200, "lng": 13.4050},
    {"code": "AMS", "city": "Amsterdam", "country": "Netherlands", "country_code": "NL", "region": "EMEA", "lat": 52.3676, "lng": 4.9041},
    {"code": "FCO", "city": "Rome", "country": "Italy", "country_code": "IT", "region": "EMEA", "lat": 41.9028, "lng": 12.4964},
    {"code": "MXP", "city": "Milan", "country": "Italy", "country_code": "IT", "region": "EMEA", "lat": 45.4642, "lng": 9.1900},
    {"code": "FLR", "city": "Florence", "country": "Italy", "country_code": "IT", "region": "EMEA", "lat": 43.7696, "lng": 11.2558},
    {"code": "MAD", "city": "Madrid", "country": "Spain", "country_code": "ES", "region": "EMEA", "lat": 40.4168, "lng": -3.7038},
    {"code": "BCN", "city": "Barcelona", "country": "Spain", "country_code": "ES", "region": "EMEA", "lat": 41.3851, "lng": 2.1734},
    {"code": "LIS", "city": "Lisbon", "country": "Portugal", "country_code": "PT", "region": "EMEA", "lat": 38.7223, "lng": -9.1393},
    {"code": "ZRH", "city": "Zurich", "country": "Switzerland", "country_code": "CH", "region": "EMEA", "lat": 47.3769, "lng": 8.5417},
    {"code": "GVA", "city": "Geneva", "country": "Switzerland", "country_code": "CH", "region": "EMEA", "lat": 46.2044, "lng": 6.1432},
    {"code": "VIE", "city": "Vienna", "country": "Austria", "country_code": "AT", "region": "EMEA", "lat": 48.2082, "lng": 16.3738},
    {"code": "DUB", "city": "Dublin", "country": "Ireland", "country_code": "IE", "region": "EMEA", "lat": 53.3498, "lng": -6.2603},
    {"code": "CPH", "city": "Copenhagen", "country": "Denmark", "country_code": "DK", "region": "EMEA", "lat": 55.6761, "lng": 12.5683},
    {"code": "ARN", "city": "Stockholm", "country": "Sweden", "country_code": "SE", "region": "EMEA", "lat": 59.3293, "lng": 18.0686},
    {"code": "IST", "city": "Istanbul", "country": "Turkey", "country_code": "TR", "region": "EMEA", "lat": 41.0082, "lng": 28.9784},
    {"code": "CAI", "city": "Cairo", "country": "Egypt", "country_code": "EG", "region": "EMEA", "lat": 30.0444, "lng": 31.2357},
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
    {"code": "DEL", "city": "New Delhi", "country": "India", "country_code": "IN", "region": "APAC", "lat": 28.6139, "lng": 77.2090}
]

ALL_AMENITIES = [
    "Spa & Hydrotherapy", "Rooftop Infinity Pool", "Michelin-starred Dining",
    "Executive Club Lounge", "Private Beach Access", "24/7 Butler Service",
    "Chauffeur Valet", "State-of-the-Art Technogym", "Wine Tasting Cellar",
    "High-speed Quantum Fiber", "Helipad Access", "Pet Concierge",
    "Artisanal Coffee Roastery", "EV Charging Stations", "Yacht Charter Service"
]

SUB_DISTRICTS = [
    "Central", "Downtown", "Harbour", "Old Town", "Financial",
    "Waterfront", "Heights", "Boulevard", "Garden", "Midtown",
    "Marina", "Uptown", "Riverside", "Southside", "West End",
    "Eastside", "North Shore", "Bayview", "Historic Quarter", "Civic Center"
]

def generate_hotels(total_target=1000):
    random.seed(42)
    hotels = []
    hotel_num = 1
    per_dest = total_target // len(DESTINATIONS)

    for dest in DESTINATIONS:
        for i in range(per_dest):
            if hotel_num > total_target:
                break
            
            # Rotate brands
            if dest["code"] in {"DPS", "MLE", "HNL"} and (i % 2 == 0):
                brand = next(b for b in BRANDS if b["brand_id"] == "solstice-resorts")
            elif i % 4 == 0:
                brand = next(b for b in BRANDS if b["brand_id"] == "novaria-grand")
            elif i % 4 == 1:
                brand = next(b for b in BRANDS if b["brand_id"] == "novaria-house")
            elif i % 4 == 2:
                brand = next(b for b in BRANDS if b["brand_id"] == "solstice-resorts")
            else:
                brand = next(b for b in BRANDS if b["brand_id"] == "novaria-select")
            
            sub = SUB_DISTRICTS[i % len(SUB_DISTRICTS)]
            hotel_id = f"NVR-{dest['code']}-{hotel_num:04d}"
            
            city_slug = dest["city"].lower().replace(" ", "-")
            sub_slug = sub.lower().replace(" ", "-")

            if brand["brand_id"] == "novaria-grand":
                name = f"The Novaria Grand {dest['city']} {sub}"
                slug = f"{city_slug}-grand-{sub_slug}"
            elif brand["brand_id"] == "novaria-house":
                name = f"Novaria House {dest['city']} {sub}"
                slug = f"{city_slug}-house-{sub_slug}"
            elif brand["brand_id"] == "solstice-resorts":
                name = f"Solstice Resort {dest['city']} {sub}"
                slug = f"{city_slug}-solstice-{sub_slug}"
            else:
                name = f"Novaria Select {dest['city']} {sub}"
                slug = f"{city_slug}-select-{sub_slug}"
            
            star_rating = brand["standards"]["min_star_rating"]
            if brand["brand_id"] in {"novaria-house", "novaria-select"} and (i % 3 == 0):
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
                "latitude": round(dest["lat"] + random.uniform(-0.05, 0.05), 4),
                "longitude": round(dest["lng"] + random.uniform(-0.05, 0.05), 4),
                "star_rating": star_rating,
                "total_rooms": random.randint(120, 650),
                "status": "Renovating" if (316 <= hotel_num <= 340) else "Active",
                "phone": f"+{random.randint(1, 99)} {random.randint(100, 999)} {random.randint(1000, 9999)}",
                "email": f"concierge.{slug}@novariahotels.com",
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
    print(f"Created Property Master DB at {db_path} with {len(hotels)} canonical hotels.")

def build_jcr_tree(hotels):
    """
    Constructs a scalable simulated JCR repository with 10,000+ nodes and deliberate audit anomalies.
    """
    jcr = {}

    # Root folders
    jcr["/content"] = {"jcr:primaryType": "sling:Folder", "jcr:title": "Content Root"}
    jcr["/content/novaria"] = {
        "jcr:primaryType": "cq:Page",
        "jcr:content": {
            "jcr:primaryType": "cq:PageContent",
            "jcr:title": "Novaria Hotels & Resorts Global Site",
            "sling:resourceType": "novaria/components/structure/homepage"
        }
    }
    
    # Common DAM asset folders
    jcr["/content/dam"] = {"jcr:primaryType": "sling:Folder"}
    jcr["/content/dam/novaria"] = {"jcr:primaryType": "sling:Folder"}
    jcr["/content/dam/novaria/content-fragments"] = {"jcr:primaryType": "sling:Folder"}
    jcr["/content/dam/novaria/content-fragments/properties"] = {"jcr:primaryType": "sling:Folder"}
    jcr["/content/dam/novaria/hotels"] = {"jcr:primaryType": "sling:Folder"}
    jcr["/content/dam/novaria/hotels/legacy/hero.jpg"] = {
        "jcr:primaryType": "dam:Asset",
        "jcr:content": {
            "jcr:primaryType": "dam:AssetContent",
            "metadata": {"dc:title": "Legacy Hotel Exterior", "dc:format": "image/jpeg"}
        }
    }
    jcr["/content/dam/novaria/destinations"] = {"jcr:primaryType": "sling:Folder"}

    # Common Experience Fragments
    jcr["/content/experience-fragments"] = {"jcr:primaryType": "sling:Folder"}
    jcr["/content/experience-fragments/novaria"] = {"jcr:primaryType": "sling:Folder"}
    jcr["/content/experience-fragments/novaria/us/en/promos"] = {"jcr:primaryType": "sling:Folder"}
    jcr["/content/experience-fragments/novaria/cards"] = {"jcr:primaryType": "sling:Folder"}
    
    # Promos
    jcr["/content/experience-fragments/novaria/us/en/promos/summer-getaway-2026"] = {
        "jcr:primaryType": "cq:Page",
        "jcr:content": {
            "jcr:primaryType": "cq:PageContent",
            "jcr:title": "Summer Getaway 2026 Promo Banner",
            "sling:resourceType": "novaria/components/xfpage",
            "campaignStatus": "active",
            "discountCode": "SUMMER26",
            "validThrough": "2027-09-30"
        }
    }

    jcr["/content/experience-fragments/novaria/us/en/promos/winter-escape-2024"] = {
        "jcr:primaryType": "cq:Page",
        "jcr:content": {
            "jcr:primaryType": "cq:PageContent",
            "jcr:title": "Winter Escape 2024 Campaign (EXPIRED)",
            "sling:resourceType": "novaria/components/xfpage",
            "campaignStatus": "expired",
            "discountCode": "WINTER24",
            "validThrough": "2024-03-31"
        }
    }

    # Templates & Models
    jcr["/conf/novaria/settings/wcm/templates/hotel-page"] = {
        "jcr:primaryType": "cq:Template",
        "jcr:title": "Hotel Detail Page Template",
        "allowedPaths": ["/content/novaria(/.*)?"]
    }
    jcr["/conf/novaria/settings/dam/cfm/models/hotel-fragment"] = {
        "jcr:primaryType": "dam:Asset",
        "jcr:title": "Hotel Content Fragment Model"
    }

    languages = [
        ("us", "en", "English (US)"),
        ("gb", "en", "English (UK)"),
        ("fr", "fr", "Français"),
        ("de", "de", "Deutsch"),
        ("jp", "ja", "日本語"),
        ("es", "es", "Español")
    ]

    for country_folder, lang, title in languages:
        root_path = f"/content/novaria/{country_folder}/{lang}"
        jcr[root_path] = {
            "jcr:primaryType": "cq:Page",
            "jcr:content": {
                "jcr:primaryType": "cq:PageContent",
                "jcr:title": title,
                "sling:resourceType": "novaria/components/structure/langroot"
            }
        }
        jcr[f"{root_path}/hotels"] = {
            "jcr:primaryType": "cq:Page",
            "jcr:content": {
                "jcr:primaryType": "cq:PageContent",
                "jcr:title": f"Explore Hotels ({title})",
                "sling:resourceType": "novaria/components/structure/hotellist"
            }
        }

    # 9 Injected Audit Anomaly Sets (350 deliberate enterprise audit gaps)
    stale_rating_hotels = set([h["hotel_id"] for h in hotels[10:50]])       # 40 hotels (star rating desync)
    broken_dam_hotels = set([h["hotel_id"] for h in hotels[50:95]])         # 45 hotels (broken hero asset in DAM)
    expired_promo_hotels = set([h["hotel_id"] for h in hotels[95:145]])     # 50 hotels (references expired 2024 promo)
    missing_french_hotels = set([h["hotel_id"] for h in hotels[145:205]])   # 60 hotels (missing French translation)
    missing_german_hotels = set([h["hotel_id"] for h in hotels[205:245]])   # 40 hotels (missing German translation)
    incomplete_cfs = set([h["hotel_id"] for h in hotels[245:280]])          # 35 hotels (CF missing contact email/phone)
    amenity_desync_hotels = set([h["hotel_id"] for h in hotels[280:315]])   # 35 hotels (AEM claims unverified amenities, PMS does not)
    renovating_desync_hotels = set([h["hotel_id"] for h in hotels[315:340]])# 25 hotels (PMS=Renovating, AEM=Active)

    # Destination Hub Nodes (50 destinations * 2 nodes = 100 nodes)
    for d in DESTINATIONS:
        dest_code = d["code"].lower()
        jcr[f"/content/novaria/us/en/destinations/{dest_code}"] = {
            "jcr:primaryType": "cq:Page",
            "jcr:content": {
                "jcr:primaryType": "cq:PageContent",
                "jcr:title": f"Destinations in {d['city']}",
                "destinationCode": d["code"],
                "region": d["region"],
                "sling:resourceType": "novaria/components/structure/destinationhub"
            }
        }
        jcr[f"/content/dam/novaria/destinations/{dest_code}/hero.jpg"] = {
            "jcr:primaryType": "dam:Asset",
            "jcr:content": {
                "jcr:primaryType": "dam:AssetContent",
                "metadata": {
                    "dc:title": f"{d['city']} Skyline",
                    "dc:format": "image/jpeg"
                }
            }
        }

    # Generate Hotel Nodes (1,000 hotels * ~10 nodes = ~10,000 nodes)
    for h in hotels:
        slug = h["slug"]
        cf_path = f"/content/dam/novaria/content-fragments/properties/{slug}"
        
        # 1. Content Fragment in DAM
        cf_data = {
            "hotelId": h["hotel_id"],
            "brandId": h["brand_id"],
            "headline": f"Experience luxury at {h['name']}",
            "overview": f"Situated in the heart of {h['city']}, {h['name']} offers an extraordinary refuge featuring {h['amenities'][0].lower()}.",
            "roomInventory": h["total_rooms"],
            "amenities": h["amenities"],
            "primaryPhone": None if h["hotel_id"] in incomplete_cfs else h["phone"],
            "contactEmail": "" if h["hotel_id"] in incomplete_cfs else h["email"]
        }

        jcr[cf_path] = {
            "jcr:primaryType": "dam:Asset",
            "jcr:content": {
                "jcr:primaryType": "dam:AssetContent",
                "contentFragment": True,
                "cq:model": "/conf/novaria/settings/dam/cfm/models/hotel-fragment",
                "data": {"master": cf_data}
            }
        }

        # 2. DAM Hero Image
        hero_dam_path = f"/content/dam/novaria/hotels/{slug}/hero.jpg"
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

        # 3. DAM Thumbnail Image
        thumb_dam_path = f"/content/dam/novaria/hotels/{slug}/thumb.jpg"
        jcr[thumb_dam_path] = {
            "jcr:primaryType": "dam:Asset",
            "jcr:content": {
                "jcr:primaryType": "dam:AssetContent",
                "metadata": {
                    "dc:title": f"{h['name']} Thumbnail",
                    "dc:format": "image/jpeg"
                }
            }
        }

        # 4. Promo card Experience Fragment
        jcr[f"/content/experience-fragments/novaria/cards/{slug}"] = {
            "jcr:primaryType": "cq:Page",
            "jcr:content": {
                "jcr:primaryType": "cq:PageContent",
                "jcr:title": f"Card - {h['name']}",
                "sling:resourceType": "novaria/components/xfpage",
                "hotelId": h["hotel_id"]
            }
        }

        # 5. Authored Page in US/EN
        page_path = f"/content/novaria/us/en/hotels/{slug}"
        authored_rating = (3 if h["star_rating"] == 4 else 4) if h["hotel_id"] in stale_rating_hotels else h["star_rating"]
        promo_ref = (
            "/content/experience-fragments/novaria/us/en/promos/winter-escape-2024"
            if h["hotel_id"] in expired_promo_hotels
            else "/content/experience-fragments/novaria/us/en/promos/summer-getaway-2026"
        )

        page_amenities = list(h["amenities"])
        if h["hotel_id"] in amenity_desync_hotels:
            unverified_candidates = ["Private Submarine Dock", "Cryotherapy Chamber", "Helipad Access", "Private Beach Access"]
            to_add = [a for a in unverified_candidates if a not in h["amenities"]]
            page_amenities.extend(to_add[:2])

        booking_status = (
            "Active"
            if h["hotel_id"] in renovating_desync_hotels
            else h["status"]
        )

        jcr[page_path] = {
            "jcr:primaryType": "cq:Page",
            "jcr:content": {
                "jcr:primaryType": "cq:PageContent",
                "jcr:title": h["name"],
                "jcr:description": f"Official page for {h['name']} in {h['city']}.",
                "cq:template": "/conf/novaria/settings/wcm/templates/hotel-page",
                "sling:resourceType": "novaria/components/structure/page",
                "cq:tags": [f"novaria:brands/{h['brand_id']}", f"novaria:destinations/{h['destination_code']}"],
                "hotelId": h["hotel_id"],
                "brandId": h["brand_id"],
                "authoredStarRating": authored_rating,
                "bookingStatus": booking_status,
                "authoredAmenities": page_amenities,
                "cq:lastModified": h["updated_at"],
                "root": {
                    "jcr:primaryType": "nt:unstructured",
                    "sling:resourceType": "novaria/components/container",
                    "hero": {
                        "jcr:primaryType": "nt:unstructured",
                        "sling:resourceType": "novaria/components/content/hero",
                        "fileReference": hero_dam_path,
                        "title": h["name"]
                    },
                    "cf_component": {
                        "jcr:primaryType": "nt:unstructured",
                        "sling:resourceType": "novaria/components/content/contentfragment",
                        "fragmentPath": cf_path
                    },
                    "promo_banner": {
                        "jcr:primaryType": "nt:unstructured",
                        "sling:resourceType": "novaria/components/content/experiencefragment",
                        "fragmentPath": promo_ref
                    }
                }
            }
        }

        # 6. Localized Copies (GB/EN, FR, DE, ES)
        gb_page_path = f"/content/novaria/gb/en/hotels/{slug}"
        jcr[gb_page_path] = {
            "jcr:primaryType": "cq:Page",
            "jcr:content": {
                "jcr:primaryType": "cq:PageContent",
                "jcr:title": h["name"],
                "cq:template": "/conf/novaria/settings/wcm/templates/hotel-page",
                "sling:resourceType": "novaria/components/structure/page",
                "hotelId": h["hotel_id"],
                "brandId": h["brand_id"],
                "authoredStarRating": authored_rating,
                "root": {"hero": {"fileReference": hero_dam_path}, "cf_component": {"fragmentPath": cf_path}}
            }
        }

        if h["hotel_id"] not in missing_french_hotels:
            fr_page_path = f"/content/novaria/fr/fr/hotels/{slug}"
            jcr[fr_page_path] = {
                "jcr:primaryType": "cq:Page",
                "jcr:content": {
                    "jcr:primaryType": "cq:PageContent",
                    "jcr:title": f"{h['name']} - Séjour d'Exception",
                    "cq:template": "/conf/novaria/settings/wcm/templates/hotel-page",
                    "sling:resourceType": "novaria/components/structure/page",
                    "hotelId": h["hotel_id"],
                    "brandId": h["brand_id"],
                    "authoredStarRating": authored_rating,
                    "root": {"hero": {"fileReference": hero_dam_path}, "cf_component": {"fragmentPath": cf_path}}
                }
            }

        if h["hotel_id"] not in missing_german_hotels:
            de_page_path = f"/content/novaria/de/de/hotels/{slug}"
            jcr[de_page_path] = {
                "jcr:primaryType": "cq:Page",
                "jcr:content": {
                    "jcr:primaryType": "cq:PageContent",
                    "jcr:title": f"{h['name']} - Luxusaufenthalt",
                    "cq:template": "/conf/novaria/settings/wcm/templates/hotel-page",
                    "sling:resourceType": "novaria/components/structure/page",
                    "hotelId": h["hotel_id"],
                    "brandId": h["brand_id"],
                    "authoredStarRating": authored_rating,
                    "root": {"hero": {"fileReference": hero_dam_path}, "cf_component": {"fragmentPath": cf_path}}
                }
            }

        es_page_path = f"/content/novaria/es/es/hotels/{slug}"
        jcr[es_page_path] = {
            "jcr:primaryType": "cq:Page",
            "jcr:content": {
                "jcr:primaryType": "cq:PageContent",
                "jcr:title": f"{h['name']} - Estancia Exclusiva",
                "cq:template": "/conf/novaria/settings/wcm/templates/hotel-page",
                "sling:resourceType": "novaria/components/structure/page",
                "hotelId": h["hotel_id"],
                "brandId": h["brand_id"],
                "authoredStarRating": authored_rating,
                "root": {"hero": {"fileReference": hero_dam_path}, "cf_component": {"fragmentPath": cf_path}}
            }
        }

        # 7. Japanese localized copy
        ja_page_path = f"/content/novaria/jp/ja/hotels/{slug}"
        jcr[ja_page_path] = {
            "jcr:primaryType": "cq:Page",
            "jcr:content": {
                "jcr:primaryType": "cq:PageContent",
                "jcr:title": f"{h['name']} - プレステージホテル",
                "cq:template": "/conf/novaria/settings/wcm/templates/hotel-page",
                "sling:resourceType": "novaria/components/structure/page",
                "hotelId": h["hotel_id"],
                "brandId": h["brand_id"],
                "authoredStarRating": authored_rating,
                "root": {"hero": {"fileReference": hero_dam_path}, "cf_component": {"fragmentPath": cf_path}}
            }
        }

        # 8. DAM Gallery Asset
        gallery_dam_path = f"/content/dam/novaria/hotels/{slug}/gallery-01.jpg"
        jcr[gallery_dam_path] = {
            "jcr:primaryType": "dam:Asset",
            "jcr:content": {
                "jcr:primaryType": "dam:AssetContent",
                "metadata": {
                    "dc:title": f"{h['name']} Suite Interior",
                    "dc:format": "image/jpeg"
                }
            }
        }

    # Injected Discrepancy 9: 20 Orphaned / Unmapped Legacy Pages (hotelId not in DB)
    for i in range(1, 21):
        orphan_slug = f"legacy-hotel-archive-{i:02d}"
        p = f"/content/novaria/us/en/hotels/{orphan_slug}"
        jcr[p] = {
            "jcr:primaryType": "cq:Page",
            "jcr:content": {
                "jcr:primaryType": "cq:PageContent",
                "jcr:title": f"Legacy Unregistered Hotel #{i}",
                "cq:template": "/conf/novaria/settings/wcm/templates/hotel-page",
                "sling:resourceType": "novaria/components/structure/page",
                "hotelId": f"NVR-DISCONTINUED-{i:03d}",
                "brandId": "novaria-grand",
                "root": {
                    "hero": {"fileReference": "/content/dam/novaria/hotels/legacy/hero.jpg"}
                }
            }
        }

    # Save to JSON store
    json_path = DATA_DIR / "jcr_mock_store.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(jcr, f, indent=2, ensure_ascii=False)

    print(f"Created Synthetic JCR Tree at {json_path} with {len(jcr)} nodes.")
    print("Injected Audit Discrepancies Summary (350 deliberate gaps):")
    print(f"  1. Stale Star Ratings: {len(stale_rating_hotels)} properties")
    print(f"  2. Broken DAM Hero Assets: {len(broken_dam_hotels)} properties")
    print(f"  3. Expired Promo Fragments: {len(expired_promo_hotels)} properties")
    print(f"  4. Missing French Translations: {len(missing_french_hotels)} properties")
    print(f"  5. Missing German Translations: {len(missing_german_hotels)} properties")
    print(f"  6. Incomplete Content Fragments: {len(incomplete_cfs)} properties")
    print(f"  7. Amenity Desyncs (AEM marketing claim vs PMS): {len(amenity_desync_hotels)} properties")
    print(f"  8. Operating Status Mismatches (Active in AEM vs Renovating in PMS): {len(renovating_desync_hotels)} properties")
    print("  9. Orphaned Unregistered Pages: 20 pages")

def main():
    print("Generating enterprise-scale synthetic datasets (1,000 properties, 10,000+ JCR nodes)...")
    hotels = generate_hotels(total_target=1000)
    build_database(hotels)
    build_jcr_tree(hotels)
    print("Enterprise dataset generation complete!")

if __name__ == "__main__":
    main()
