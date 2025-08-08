#!/usr/bin/env python3
"""Lookup missing place IDs."""

import os
from pathlib import Path
import logging

from receipt_label.data.places_api import PlacesAPI
from receipt_dynamo.data._pulumi import load_secrets

logger = logging.getLogger(__name__)


def _fetch_google_places_api_key_from_pulumi() -> str:
    """Fetch secret from Pulumi config if env var is missing."""
    stack = os.environ.get("PULUMI_STACK", "dev")
    secrets = load_secrets(
        stack, project_dir=Path(__file__).resolve().parent / "infra"
    )

    if "portfolio:GOOGLE_PLACES_API_KEY" in secrets:
        return secrets["portfolio:GOOGLE_PLACES_API_KEY"]["value"]
    return ""


# Initialize the API with Google Maps API key
api_key = _fetch_google_places_api_key_from_pulumi()
if not api_key:
    print("❌ GOOGLE_MAPS_API_KEY not found")
    exit(1)

api = PlacesAPI(api_key)

print("=" * 70)
print("SEARCHING FOR MISSING PLACE IDs")
print("=" * 70)

# Search for DIY on Roadside
print("\n1. Searching for DIY on Roadside...")
queries = [
    "DIY on Roadside",
    "DIY Roadside service",
    "DIY Roadside assistance"
]

for query in queries:
    result = api.search_by_text(query)
    if result and result.get("place_id"):
        print(f"✅ Found with query '{query}':")
        print(f"   Name: {result.get('name')}")
        print(f"   Place ID: {result.get('place_id')}")
        print(f"   Address: {result.get('formatted_address')}")
        break
else:
    print("❌ DIY on Roadside not found")

# Search for Ace Hardware in Stockton
print("\n2. Searching for Ace Hardware at 3201 W Benjamin Holt Dr, Stockton...")
queries = [
    "Ace Hardware 3201 W Benjamin Holt Dr Stockton CA 95219",
    "Stockton Ace Hardware (209) 951-8050",
    "Ace Hardware Stockton CA Benjamin Holt"
]

for query in queries:
    result = api.search_by_text(query)
    if result and result.get("place_id"):
        print(f"✅ Found with query '{query}':")
        print(f"   Name: {result.get('name')}")
        print(f"   Place ID: {result.get('place_id')}")
        print(f"   Address: {result.get('formatted_address')}")
        print(f"   Phone: {result.get('formatted_phone_number', 'N/A')}")
        break
else:
    print("❌ Ace Hardware not found")
    
    # Try phone lookup
    print("\n   Trying phone lookup for (209) 951-8050...")
    result = api.search_by_phone("(209) 951-8050")
    if result and result.get("place_id"):
        print(f"   ✅ Found via phone:")
        print(f"      Name: {result.get('name')}")
        print(f"      Place ID: {result.get('place_id')}")
        print(f"      Address: {result.get('formatted_address')}")