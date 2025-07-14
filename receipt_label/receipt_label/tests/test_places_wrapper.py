"""Tests for Google Places API wrapped client with context manager integration."""

import threading
from unittest.mock import MagicMock, Mock, patch

import pytest
from receipt_dynamo.entities.ai_usage_metric import AIUsageMetric

from receipt_label.utils.ai_usage_context import ai_usage_context
from receipt_label.utils.ai_usage_tracker import AIUsageTracker


class TestPlacesWrapper:
    """Test Google Places client wrapper functionality."""

    def test_create_wrapped_places_client(self):
        """Test basic wrapped client creation."""
        # Create mocks
        dynamo_client = Mock()
        places_client = Mock()
        tracker = AIUsageTracker(
            dynamo_client=dynamo_client, track_to_dynamo=True
        )

        # Create wrapped client
        wrapped_client = AIUsageTracker.create_wrapped_places_client(
            places_client, tracker
        )

        # Verify we can access the wrapped client
        assert wrapped_client is not None
        assert hasattr(wrapped_client, "_client")
        assert hasattr(wrapped_client, "_tracker")

    def test_wrapped_places_method_tracking(self):
        """Test that Places API methods are tracked."""
        # Create mocks
        dynamo_client = Mock()
        places_client = Mock()
        places_client.places.return_value = {"results": ["place1", "place2"]}

        tracker = AIUsageTracker(dynamo_client=dynamo_client)
        wrapped_client = AIUsageTracker.create_wrapped_places_client(
            places_client, tracker
        )

        # Call a tracked method
        result = wrapped_client.places("restaurants near me")

        # Verify the method was called
        places_client.places.assert_called_once_with("restaurants near me")
        assert result == {"results": ["place1", "place2"]}

        # Verify metric was stored
        dynamo_client.put_item.assert_called_once()
        call_args = dynamo_client.put_item.call_args
        item = call_args.kwargs["Item"]
        assert item["service"]["S"] == "google_places"
        assert item["model"]["S"] == "places_api"
        assert item["operation"]["S"] == "places"

    def test_context_propagation_to_places_calls(self):
        """Test that context from ai_usage_context propagates to Places API calls."""
        # Create mocks
        dynamo_client = Mock()
        places_client = Mock()
        places_client.places_nearby.return_value = {"results": []}

        # Use context manager
        with ai_usage_context(
            "location_processing",
            job_id="job-123",
            batch_id="batch-456",
            track_to_dynamo=True,
        ) as tracker:
            # Replace the dynamo client
            tracker.dynamo_client = dynamo_client
            tracker.track_to_dynamo = True

            # Create wrapped client
            wrapped_client = AIUsageTracker.create_wrapped_places_client(
                places_client, tracker
            )

            # Make a call
            wrapped_client.places_nearby(
                location=(37.7749, -122.4194), radius=1000
            )

        # Verify the job_id was propagated
        dynamo_client.put_item.assert_called_once()
        call_args = dynamo_client.put_item.call_args
        item = call_args.kwargs["Item"]
        assert item["jobId"]["S"] == "job-123"
        # Check metadata
        metadata_map = item["metadata"]["M"]
        assert metadata_map["job_id"]["S"] == "job-123"
        assert metadata_map["batch_id"]["S"] == "batch-456"

    def test_nested_context_with_places(self):
        """Test nested contexts work correctly with Places API."""
        # Create mocks
        dynamo_client = Mock()
        places_client = Mock()
        places_client.place.return_value = {"result": {"name": "Test Place"}}

        # Nested contexts
        with ai_usage_context(
            "outer_operation", job_id="outer-job", track_to_dynamo=True
        ) as outer_tracker:
            outer_tracker.dynamo_client = dynamo_client
            outer_tracker.track_to_dynamo = True

            wrapped_client = AIUsageTracker.create_wrapped_places_client(
                places_client, outer_tracker
            )

            # First call in outer context
            wrapped_client.place("place_id_1")

            with ai_usage_context(
                "inner_operation",
                tracker=outer_tracker,
                job_id="inner-job",
                track_to_dynamo=True,
            ):
                # Second call in inner context
                wrapped_client.place("place_id_2")

            # Third call back in outer context
            wrapped_client.place("place_id_3")

        # Verify all calls were made
        assert dynamo_client.put_item.call_count == 3

        # Check job_ids
        calls = dynamo_client.put_item.call_args_list
        assert (
            calls[0].kwargs["Item"]["jobId"]["S"] == "outer-job"
        )  # First call
        assert (
            calls[1].kwargs["Item"]["jobId"]["S"] == "inner-job"
        )  # Second call (nested)
        assert (
            calls[2].kwargs["Item"]["jobId"]["S"] == "outer-job"
        )  # Third call (back to outer)

    def test_multiple_places_methods_tracked(self):
        """Test that multiple Places API methods are tracked."""
        # Create mocks
        dynamo_client = Mock()
        places_client = Mock()

        # Mock different methods
        places_client.places.return_value = {"results": []}
        places_client.places_nearby.return_value = {"results": []}
        places_client.place.return_value = {"result": {}}
        places_client.find_place.return_value = {"candidates": []}

        tracker = AIUsageTracker(dynamo_client=dynamo_client)
        wrapped_client = AIUsageTracker.create_wrapped_places_client(
            places_client, tracker
        )

        # Call different methods
        wrapped_client.places("query")
        wrapped_client.places_nearby(location=(0, 0))
        wrapped_client.place("place_id")
        wrapped_client.find_place("input", "textquery")

        # Verify all were tracked
        assert dynamo_client.put_item.call_count == 4

        # Check operations
        operations = [
            call.kwargs["Item"]["operation"]["S"]
            for call in dynamo_client.put_item.call_args_list
        ]
        assert "places" in operations
        assert "places_nearby" in operations
        assert "place" in operations
        assert "find_place" in operations

    def test_non_places_methods_not_tracked(self):
        """Test that non-Places API methods are not tracked."""
        # Create mocks
        dynamo_client = Mock()
        places_client = Mock()
        places_client.geocode.return_value = {
            "results": []
        }  # Non-places method

        tracker = AIUsageTracker(dynamo_client=dynamo_client)
        wrapped_client = AIUsageTracker.create_wrapped_places_client(
            places_client, tracker
        )

        # Call non-tracked method
        result = wrapped_client.geocode("1600 Amphitheatre Parkway")

        # Verify it was called but not tracked
        places_client.geocode.assert_called_once()
        dynamo_client.put_item.assert_not_called()

    def test_error_handling_in_wrapped_places(self):
        """Test that errors are properly handled and tracked."""
        # Create mocks
        dynamo_client = Mock()
        places_client = Mock()
        places_client.places.side_effect = Exception("API Error")

        tracker = AIUsageTracker(dynamo_client=dynamo_client)
        wrapped_client = AIUsageTracker.create_wrapped_places_client(
            places_client, tracker
        )

        # Call should raise but still track
        with pytest.raises(Exception, match="API Error"):
            wrapped_client.places("query")

        # Verify error was tracked
        dynamo_client.put_item.assert_called_once()
        call_args = dynamo_client.put_item.call_args
        item = call_args.kwargs["Item"]
        assert item["error"]["S"] == "API Error"

    def test_thread_safety_places_wrapper(self):
        """Test that wrapped Places client is thread-safe with contexts."""
        # Create mocks
        dynamo_client = Mock()
        places_client = Mock()
        places_client.place.return_value = {"result": {}}

        results = []

        def thread_func(job_id: str):
            with ai_usage_context(
                f"thread_{job_id}", job_id=job_id, track_to_dynamo=True
            ) as tracker:
                tracker.dynamo_client = dynamo_client
                tracker.track_to_dynamo = True
                wrapped = AIUsageTracker.create_wrapped_places_client(
                    places_client, tracker
                )
                wrapped.place(f"place_{job_id}")
                # Store the job_id that was used
                call_args = dynamo_client.put_item.call_args
                if call_args:
                    results.append(call_args.kwargs["Item"]["jobId"]["S"])

        # Run in threads
        threads = []
        for i in range(3):
            t = threading.Thread(target=thread_func, args=(f"job-{i}",))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        # Each thread should have its own job_id
        assert len(set(results)) == 3  # All job_ids should be unique
        assert all(job_id.startswith("job-") for job_id in results)
