#!/usr/bin/env python3
"""Lookup Neighborly place ID using Places API."""

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
    logger.warning(
        "Could not read GOOGLE_PLACES_API_KEY from Pulumi (stack=%s): %s",
        stack,
        "GOOGLE_PLACES_API_KEY not found in Pulumi secrets",
    )
    return ""


# Initialize the API with Google Maps API key
api_key = _fetch_google_places_api_key_from_pulumi()
if not api_key:
    print("❌ GOOGLE_MAPS_API_KEY environment variable not set")
    exit(1)

api = PlacesAPI(api_key)

# Search for Neighborly
print(
    "Searching for Neighborly at 4000 E Thousand Oaks Blvd, Westlake Village, CA 91362..."
)

# Try text search
result = api.search_by_text(
    "Neighborly 4000 E Thousand Oaks Blvd Westlake Village CA 91362"
)

if result and result.get("place_id"):
    print("✅ Found via text search:")
    print(f"   Name: {result.get('name')}")
    print(f"   Place ID: {result.get('place_id')}")
    print(f"   Address: {result.get('formatted_address')}")
    print(f"   Phone: {result.get('formatted_phone_number', 'N/A')}")
else:
    print("❌ Not found via text search")

    # Try address lookup
    print("\nTrying address lookup...")
    result = api.search_by_address(
        "4000 E Thousand Oaks Blvd Westlake Village CA 91362"
    )

    if result and result.get("place_id"):
        print("✅ Found via address:")
        print(f"   Name: {result.get('name')}")
        print(f"   Place ID: {result.get('place_id')}")
        print(f"   Address: {result.get('formatted_address')}")
    else:
        print("❌ Not found via address lookup either")
