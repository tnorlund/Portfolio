#!/usr/bin/env python3
"""Test the label validation count API performance."""

import os
import time

import requests


def main():
    """Test API performance."""
    # Get API URL from environment or use dev
    api_url = os.environ.get("API_URL", "https://dev-api.tylernorlund.com")
    endpoint = f"{api_url}/label_validation_count"

    print(f"Testing API endpoint: {endpoint}")
    print("-" * 60)

    # Make multiple requests to test performance
    for i in range(3):
        print(f"\nRequest #{i+1}:")

        start_time = time.time()
        response = requests.get(endpoint)
        end_time = time.time()

        duration_ms = (end_time - start_time) * 1000

        print(f"  Status Code: {response.status_code}")
        print(f"  Response Time: {duration_ms:.0f}ms")

        if response.status_code == 200:
            data = response.json()
            print(f"  Labels returned: {len(data)}")
            # Show first label as sample
            if data:
                first_label = list(data.keys())[0]
                print(f"  Sample - {first_label}: {data[first_label]}")
        else:
            print(f"  Error: {response.text}")

        # Brief pause between requests
        if i < 2:
            time.sleep(1)

    print("\n" + "=" * 60)
    print("ANALYSIS:")
    print("- Response times >500ms indicate cache misses (querying live data)")
    print("- Response times <100ms indicate cache hits")
    print("- The cache updater Lambda runs every 5 minutes")


if __name__ == "__main__":
    main()
