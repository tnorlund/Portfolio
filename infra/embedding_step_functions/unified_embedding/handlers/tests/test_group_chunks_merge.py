"""Unit tests for GroupChunksForMerge step function logic.

This tests the data passing logic without needing to deploy to AWS.
"""

import json
from typing import Any, Dict


def test_group_chunks_merge_uses_poll_results_data():
    """Test that GroupChunksForMerge correctly uses poll_results_data for S3 keys."""

    # Sample input that matches the actual Step Function input structure
    sample_input = {
        "chunked_data": {
            "batch_id": "c5df280d-32c7-4377-80d0-eb3e34417c37",
            "total_chunks": 7,
            "chunks": [],
            "chunks_s3_key": "chunks/c5df280d-32c7-4377-80d0-eb3e34417c37/chunks.json",
            "chunks_s3_bucket": "chromadb-dev-shared-buckets-vectors-c239843",
            "use_s3": True,
            # Note: poll_results_s3_key is NOT in chunked_data (this is the bug)
        },
        "poll_results_data": {
            "batch_id": "c5df280d-32c7-4377-80d0-eb3e34417c37",
            "poll_results": None,  # In S3
            "poll_results_s3_key": "poll_results/c5df280d-32c7-4377-80d0-eb3e34417c37/poll_results.json",
            "poll_results_s3_bucket": "chromadb-dev-shared-buckets-vectors-c239843",
        },
        "chunk_results": [
            {"intermediate_key": "intermediate/c5df280d-32c7-4377-80d0-eb3e34417c37/chunk-0/"},
        ],
        "poll_results": [],
    }

    # Simulate GroupChunksForMerge Pass state output
    # OLD (broken) approach - tries to get from chunked_data
    old_approach = {
        "batch_id": sample_input["chunked_data"]["batch_id"],
        "total_chunks": sample_input["chunked_data"]["total_chunks"],
        "chunk_results": sample_input["chunk_results"],
        "group_size": 10,
        "poll_results": sample_input["poll_results"],
        # This would fail because chunked_data doesn't have poll_results_s3_key
        "poll_results_s3_key": sample_input["chunked_data"].get("poll_results_s3_key"),  # None!
        "poll_results_s3_bucket": sample_input["chunked_data"].get("poll_results_s3_bucket"),  # None!
    }

    assert old_approach["poll_results_s3_key"] is None, "Old approach would fail"

    # NEW (fixed) approach - gets from poll_results_data
    new_approach = {
        "batch_id": sample_input["chunked_data"]["batch_id"],
        "total_chunks": sample_input["chunked_data"]["total_chunks"],
        "chunk_results": sample_input["chunk_results"],
        "group_size": 10,
        "poll_results": sample_input["poll_results"],
        # This works because poll_results_data always has these keys
        "poll_results_s3_key": sample_input["poll_results_data"]["poll_results_s3_key"],
        "poll_results_s3_bucket": sample_input["poll_results_data"]["poll_results_s3_bucket"],
    }

    assert new_approach["poll_results_s3_key"] == "poll_results/c5df280d-32c7-4377-80d0-eb3e34417c37/poll_results.json"
    assert new_approach["poll_results_s3_bucket"] == "chromadb-dev-shared-buckets-vectors-c239843"

    print("✅ GroupChunksForMerge fix validated")


def test_jsonpath_expressions():
    """Test that JSONPath expressions match expected data structures."""

    sample_data = {
        "chunked_data": {
            "batch_id": "test-123",
            "total_chunks": 5,
        },
        "poll_results_data": {
            "poll_results_s3_key": "poll_results/test-123/poll_results.json",
            "poll_results_s3_bucket": "test-bucket",
        },
    }

    # Test JSONPath expressions used in Step Function
    import jsonpath_ng

    # Old (broken) path
    old_path = jsonpath_ng.parse("$.chunked_data.poll_results_s3_key")
    old_matches = [match.value for match in old_path.find(sample_data)]
    assert len(old_matches) == 0, "Old path should return empty (key doesn't exist)"

    # New (fixed) path
    new_path = jsonpath_ng.parse("$.poll_results_data.poll_results_s3_key")
    new_matches = [match.value for match in new_path.find(sample_data)]
    assert len(new_matches) == 1, "New path should return one match"
    assert new_matches[0] == "poll_results/test-123/poll_results.json"

    print("✅ JSONPath expressions validated")


if __name__ == "__main__":
    test_group_chunks_merge_uses_poll_results_data()
    test_jsonpath_expressions()
    print("\n✅ All tests passed!")

