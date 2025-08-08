#!/usr/bin/env python3
"""Find Neighborly place ID."""

import os
from receipt_label.data.places_api import PlacesAPIClient

# Initialize client
client = PlacesAPIClient()

# Search for Neighborly
print("Searching for Neighborly...")
result = client.text_search(
    query="Neighborly 4000 E Thousand Oaks Blvd Westlake Village CA 91362"
)

if result and result.place_id:
    print(f"✅ Found: {result.name}")
    print(f"   Place ID: {result.place_id}")
    print(f"   Address: {result.address}")
    print(f"   Phone: {result.phone_number}")
else:
    print("❌ No results found")