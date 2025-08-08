#!/usr/bin/env python3
"""
Simple script to look up Google Places ID for Ranch Hand BBQ.
"""

import os
import json
import requests
from pathlib import Path


def search_place_by_address(name: str, address: str, api_key: str = None):
    """Search for a place by name and address."""
    
    if not api_key:
        api_key = os.environ.get("GOOGLE_PLACES_API_KEY")
        if not api_key:
            # Try to get from Pulumi
            from receipt_dynamo.data._pulumi import load_secrets
            stack = os.environ.get("PULUMI_STACK", "dev")
            secrets = load_secrets(stack, project_dir=Path.cwd() / "infra")
            if "portfolio:GOOGLE_PLACES_API_KEY" in secrets:
                api_key = secrets["portfolio:GOOGLE_PLACES_API_KEY"]["value"]
            else:
                print("❌ No Google Places API key found in environment or Pulumi")
                return None
    
    # Text search endpoint
    url = "https://maps.googleapis.com/maps/api/place/textsearch/json"
    
    # Search for the exact business
    query = f"{name} {address}"
    
    params = {
        "query": query,
        "key": api_key
    }
    
    print(f"🔍 Searching for: {query}")
    
    try:
        response = requests.get(url, params=params)
        data = response.json()
        
        if data.get("status") == "OK" and data.get("results"):
            # Show all results
            print(f"\n📍 Found {len(data['results'])} result(s):\n")
            
            for i, place in enumerate(data["results"], 1):
                print(f"{i}. {place.get('name')}")
                print(f"   Address: {place.get('formatted_address')}")
                print(f"   Place ID: {place.get('place_id')}")
                print(f"   Types: {', '.join(place.get('types', []))}")
                
                # Get phone number from place details
                if place.get('place_id'):
                    details_url = "https://maps.googleapis.com/maps/api/place/details/json"
                    details_params = {
                        "place_id": place['place_id'],
                        "fields": "formatted_phone_number",
                        "key": api_key
                    }
                    details_response = requests.get(details_url, params=details_params)
                    details_data = details_response.json()
                    if details_data.get("status") == "OK" and details_data.get("result"):
                        phone = details_data["result"].get("formatted_phone_number")
                        if phone:
                            print(f"   Phone: {phone}")
                print()
            
            # Return the first result
            best_match = data["results"][0]
            return {
                "place_id": best_match.get("place_id"),
                "name": best_match.get("name"),
                "address": best_match.get("formatted_address"),
                "types": best_match.get("types", [])
            }
        else:
            print(f"❌ No results found. Status: {data.get('status')}")
            if data.get("error_message"):
                print(f"   Error: {data['error_message']}")
    
    except Exception as e:
        print(f"❌ Error searching: {e}")
    
    return None


def update_additional_metadata():
    """Update the additional_metadata.json file with Place ID."""
    
    # Look up Ranch Hand BBQ
    result = search_place_by_address(
        name="Ranch Hand BBQ",
        address="1015 Broadbeck Dr, Newbury Park, CA 91320"
    )
    
    if not result:
        print("\n⚠️  Could not find Ranch Hand BBQ Place ID")
        return
    
    print(f"\n✅ Found Ranch Hand BBQ!")
    print(f"   Place ID: {result['place_id']}")
    print(f"   Verified Address: {result['address']}")
    
    # Update the additional_metadata.json file
    file_path = Path("additional_metadata.json")
    with open(file_path, "r") as f:
        data = json.load(f)
    
    # Update Ranch Hand BBQ entry
    for entry in data["metadata"]:
        if entry["merchant_name"] == "Ranch Hand BBQ":
            entry["place_id"] = result["place_id"]
            entry["address"] = result["address"]
            del entry["needs_place_id_lookup"]  # Remove the flag
            print(f"\n✅ Updated Ranch Hand BBQ metadata with Place ID")
            break
    
    # Save the updated file
    with open(file_path, "w") as f:
        json.dump(data, f, indent=2)
    
    print(f"💾 Saved updated metadata to {file_path}")
    
    # Show final summary
    print("\n" + "="*60)
    print("READY FOR UPLOAD:")
    print("="*60)
    for entry in data["metadata"]:
        print(f"\n{entry['merchant_name']}:")
        print(f"  Image ID: {entry['image_id']}")
        print(f"  Place ID: {entry['place_id']}")
        print(f"  Address: {entry['address']}")


if __name__ == "__main__":
    update_additional_metadata()