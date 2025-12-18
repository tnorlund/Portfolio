"""Tests for N-way merge optimization.

These tests verify that the prepare_merge_pairs handler correctly groups
intermediates into N-way groups (default: 10) instead of pairs (2).
"""

import json
import os
from typing import Any, Dict, List

import pytest


class TestNWayMerge:
    """Test N-way merge grouping logic."""

    def test_merge_group_size_10(self, monkeypatch):
        """Test that intermediates are grouped into groups of 10."""
        monkeypatch.setenv("MERGE_GROUP_SIZE", "10")

        # Need to reimport after setting env var
        import handler

        # Reload module to pick up new env var
        import importlib

        importlib.reload(handler)

        # Create 25 intermediates
        intermediates = [
            {"intermediate_key": f"intermediate/batch/chunk-{i}/"}
            for i in range(25)
        ]

        event = {
            "batch_id": "test-batch",
            "database": "lines",
            "intermediates": intermediates,
            "round": 0,
        }

        result = handler.handle(event, None)

        # Should create 3 groups: [10, 10, 5]
        assert result["done"] is False
        assert result["pair_count"] == 3
        assert len(result["pairs"]) == 3

        # Verify group sizes
        assert len(result["pairs"][0]["intermediates"]) == 10
        assert len(result["pairs"][1]["intermediates"]) == 10
        assert len(result["pairs"][2]["intermediates"]) == 5

        # Verify all intermediates are included
        total_intermediates = sum(
            len(group["intermediates"]) for group in result["pairs"]
        )
        assert total_intermediates == 25

    def test_merge_group_size_configurable(self, monkeypatch):
        """Test that MERGE_GROUP_SIZE is configurable."""
        monkeypatch.setenv("MERGE_GROUP_SIZE", "8")

        # Reimport to pick up new env var
        import handler
        import importlib

        importlib.reload(handler)

        intermediates = [
            {"intermediate_key": f"intermediate/batch/chunk-{i}/"}
            for i in range(20)
        ]

        event = {
            "batch_id": "test-batch",
            "database": "lines",
            "intermediates": intermediates,
            "round": 0,
        }

        result = handler.handle(event, None)

        # Should create 3 groups: [8, 8, 4]
        assert result["pair_count"] == 3
        assert len(result["pairs"][0]["intermediates"]) == 8
        assert len(result["pairs"][1]["intermediates"]) == 8
        assert len(result["pairs"][2]["intermediates"]) == 4

    def test_single_intermediate_returns_done(self, monkeypatch):
        """Test that single intermediate returns done=True."""
        monkeypatch.setenv("MERGE_GROUP_SIZE", "10")

        import handler
        import importlib

        importlib.reload(handler)

        event = {
            "batch_id": "test-batch",
            "database": "lines",
            "intermediates": [{"intermediate_key": "intermediate/batch/chunk-0/"}],
            "round": 0,
        }

        result = handler.handle(event, None)

        assert result["done"] is True
        assert (
            result["single_intermediate"]["intermediate_key"]
            == "intermediate/batch/chunk-0/"
        )

    def test_zero_intermediates_returns_done(self, monkeypatch):
        """Test that zero intermediates returns done=True."""
        monkeypatch.setenv("MERGE_GROUP_SIZE", "10")

        import handler
        import importlib

        importlib.reload(handler)

        event = {
            "batch_id": "test-batch",
            "database": "lines",
            "intermediates": [],
            "round": 0,
        }

        result = handler.handle(event, None)

        assert result["done"] is True
        assert result["single_intermediate"] is None
        assert "message" in result

    def test_grouping_preserves_order(self, monkeypatch):
        """Test that intermediates are grouped in order."""
        monkeypatch.setenv("MERGE_GROUP_SIZE", "3")

        import handler
        import importlib

        importlib.reload(handler)

        intermediates = [
            {"intermediate_key": f"intermediate/batch/chunk-{i}/"}
            for i in range(10)
        ]

        event = {
            "batch_id": "test-batch",
            "database": "lines",
            "intermediates": intermediates,
            "round": 0,
        }

        result = handler.handle(event, None)

        # Should create 4 groups: [3, 3, 3, 1]
        assert result["pair_count"] == 4

        # Verify first group has chunks 0-2
        group_0_keys = [
            item["intermediate_key"] for item in result["pairs"][0]["intermediates"]
        ]
        assert group_0_keys == [
            "intermediate/batch/chunk-0/",
            "intermediate/batch/chunk-1/",
            "intermediate/batch/chunk-2/",
        ]

        # Verify second group has chunks 3-5
        group_1_keys = [
            item["intermediate_key"] for item in result["pairs"][1]["intermediates"]
        ]
        assert group_1_keys == [
            "intermediate/batch/chunk-3/",
            "intermediate/batch/chunk-4/",
            "intermediate/batch/chunk-5/",
        ]

    def test_filters_invalid_intermediates(self, monkeypatch):
        """Test that invalid/error intermediates are filtered out."""
        monkeypatch.setenv("MERGE_GROUP_SIZE", "10")

        import handler
        import importlib

        importlib.reload(handler)

        intermediates = [
            {"intermediate_key": "intermediate/batch/chunk-0/"},
            {"statusCode": 500, "error": "Processing failed"},  # Error response
            {"intermediate_key": "intermediate/batch/chunk-1/"},
            {"empty": True, "message": "Empty chunk"},  # Empty chunk
            {"intermediate_key": "intermediate/batch/chunk-2/"},
        ]

        event = {
            "batch_id": "test-batch",
            "database": "lines",
            "intermediates": intermediates,
            "round": 0,
        }

        result = handler.handle(event, None)

        # Should only process valid intermediates (3 total)
        # With group size 10, all 3 should be in 1 group
        assert result["pair_count"] == 1
        assert len(result["pairs"][0]["intermediates"]) == 3

    def test_round_increments_correctly(self, monkeypatch):
        """Test that round number increments correctly."""
        monkeypatch.setenv("MERGE_GROUP_SIZE", "2")

        import handler
        import importlib

        importlib.reload(handler)

        intermediates = [
            {"intermediate_key": f"intermediate/batch/chunk-{i}/"}
            for i in range(4)
        ]

        event = {
            "batch_id": "test-batch",
            "database": "lines",
            "intermediates": intermediates,
            "round": 3,  # Round 3
        }

        result = handler.handle(event, None)

        # Next round should be 4
        assert result["round"] == 4

        # Batch IDs should include round number
        for group in result["pairs"]:
            assert group["batch_id"] == "test-batch-r4"
            assert group["round"] == 4

    def test_backward_compatibility_field_names(self, monkeypatch):
        """Test that field names maintain backward compatibility."""
        monkeypatch.setenv("MERGE_GROUP_SIZE", "10")

        import handler
        import importlib

        importlib.reload(handler)

        intermediates = [
            {"intermediate_key": f"intermediate/batch/chunk-{i}/"}
            for i in range(25)
        ]

        event = {
            "batch_id": "test-batch",
            "database": "lines",
            "intermediates": intermediates,
            "round": 0,
        }

        result = handler.handle(event, None)

        # Should use "pairs" field name (even though they're groups)
        assert "pairs" in result
        assert "pair_count" in result  # Not "group_count"

        # Each group should have "pair_index" (even though it's group_index)
        for idx, group in enumerate(result["pairs"]):
            assert "pair_index" in group
            assert group["pair_index"] == idx

    def test_pass_through_poll_results_keys(self, monkeypatch):
        """Test that poll_results S3 keys are passed through."""
        monkeypatch.setenv("MERGE_GROUP_SIZE", "10")

        import handler
        import importlib

        importlib.reload(handler)

        intermediates = [
            {"intermediate_key": f"intermediate/batch/chunk-{i}/"}
            for i in range(5)
        ]

        event = {
            "batch_id": "test-batch",
            "database": "lines",
            "intermediates": intermediates,
            "round": 0,
            "poll_results_s3_key": "poll_results/test-batch/poll_results.json",
            "poll_results_s3_bucket": "test-bucket",
        }

        result = handler.handle(event, None)

        # Should pass through poll_results keys
        assert result["poll_results_s3_key"] == "poll_results/test-batch/poll_results.json"
        assert result["poll_results_s3_bucket"] == "test-bucket"


class TestNWayMergeEdgeCases:
    """Test edge cases for N-way merge."""

    def test_large_group_size(self, monkeypatch):
        """Test with group size larger than intermediate count."""
        monkeypatch.setenv("MERGE_GROUP_SIZE", "100")

        import handler
        import importlib

        importlib.reload(handler)

        intermediates = [
            {"intermediate_key": f"intermediate/batch/chunk-{i}/"}
            for i in range(10)
        ]

        event = {
            "batch_id": "test-batch",
            "database": "lines",
            "intermediates": intermediates,
            "round": 0,
        }

        result = handler.handle(event, None)

        # Should create 1 group with all 10 intermediates
        assert result["pair_count"] == 1
        assert len(result["pairs"][0]["intermediates"]) == 10

    def test_exact_multiple_of_group_size(self, monkeypatch):
        """Test when intermediate count is exact multiple of group size."""
        monkeypatch.setenv("MERGE_GROUP_SIZE", "10")

        import handler
        import importlib

        importlib.reload(handler)

        intermediates = [
            {"intermediate_key": f"intermediate/batch/chunk-{i}/"}
            for i in range(30)  # Exactly 3 groups
        ]

        event = {
            "batch_id": "test-batch",
            "database": "lines",
            "intermediates": intermediates,
            "round": 0,
        }

        result = handler.handle(event, None)

        # Should create exactly 3 groups of 10
        assert result["pair_count"] == 3
        for group in result["pairs"]:
            assert len(group["intermediates"]) == 10


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
