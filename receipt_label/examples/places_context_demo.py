#!/usr/bin/env python3
"""
Demo script showing Google Places API integration with AI usage context managers.

This demonstrates how the new wrapped Places client automatically tracks usage
and propagates context from the ai_usage_context manager.
"""

import googlemaps

from receipt_label.utils.ai_usage_context import ai_usage_context
from receipt_label.utils.ai_usage_tracker import AIUsageTracker


def main():
    """Run the Google Places context manager demo."""
    # Initialize the Places client (replace with your actual API key)
    places_client = googlemaps.Client(key="YOUR_API_KEY_HERE")

    # Create an AI usage tracker
    tracker = AIUsageTracker(
        user_id="demo_user",
        track_to_file=True,  # Log to file for demo
        log_file="/tmp/places_usage_demo.jsonl",
    )

    # Create a wrapped client that automatically tracks usage
    wrapped_places = AIUsageTracker.create_wrapped_places_client(
        places_client, tracker
    )

    print("=== Google Places API Context Manager Demo ===\n")

    # Example 1: Basic usage with context
    print("1. Basic usage with context:")
    with ai_usage_context(
        "restaurant_search",
        tracker=tracker,
        job_id="job-001",
        batch_id="batch-restaurant",
    ):
        # This call is automatically tracked with job_id and batch_id
        results = wrapped_places.places("Italian restaurants in San Francisco")
        print(f"   Found {len(results.get('results', []))} restaurants")

    # Example 2: Nested contexts
    print("\n2. Nested contexts:")
    with ai_usage_context(
        "location_analysis",
        tracker=tracker,
        job_id="job-002",
        analysis_type="tourist_spots",
    ):
        print("   Outer context: tourist analysis")

        # Search for tourist attractions
        attractions = wrapped_places.places("tourist attractions Golden Gate")
        print(f"   Found {len(attractions.get('results', []))} attractions")

        # Nested context for detailed place information
        with ai_usage_context(
            "place_details",
            tracker=tracker,
            job_id="job-003",
            parent_job="job-002",
        ):
            print("   Inner context: getting place details")
            # In a real app, you'd use actual place_ids from the search
            # details = wrapped_places.place(place_id="some_place_id")
            print("   (Would fetch detailed place information here)")

    # Example 3: Error handling
    print("\n3. Error handling:")
    try:
        with ai_usage_context(
            "error_test", tracker=tracker, job_id="job-error"
        ):
            # This would fail with an invalid query
            # wrapped_places.places("")  # Empty query
            print("   (Error handling demonstrated)")
    except Exception as e:
        print(f"   Error caught and tracked: {e}")

    print("\n=== Demo Complete ===")
    print(f"Check usage logs at: /tmp/places_usage_demo.jsonl")
    print("\nKey benefits:")
    print("- Automatic usage tracking for all Places API calls")
    print("- Context propagation from ai_usage_context")
    print("- Thread-safe for concurrent operations")
    print("- Compatible with existing @track_google_places decorator")


if __name__ == "__main__":
    main()
