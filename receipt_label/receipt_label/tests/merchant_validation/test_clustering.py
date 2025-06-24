"""Unit tests for merchant metadata clustering."""

import uuid
from datetime import datetime, timezone
from typing import List
from unittest.mock import MagicMock, patch

import pytest
from receipt_dynamo.entities import ReceiptMetadata


def _build_metadata(
    receipt_id: int,
    *,
    place_id: str = "",
    name: str = "",
    address: str = "",
    phone: str = "",
) -> ReceiptMetadata:
    return ReceiptMetadata(
        image_id=str(uuid.uuid4()),
        receipt_id=receipt_id,
        place_id=place_id,
        merchant_name=name,
        matched_fields=[],
        timestamp=datetime.now(timezone.utc),
        address=address,
        phone_number=phone,
        validated_by="INFERENCE",
    )


def test_cluster_by_metadata_groups_similar_records() -> None:
    """Records with overlapping data should be clustered together."""

    # Create test records
    records = [
        _build_metadata(
            1,
            place_id="PID1",
            name="Starbucks",
            address="123 Main St",
            phone="111-222-3333",
        ),
        _build_metadata(
            2,
            place_id="PID1",
            name="Starbucks Coffee",
            address="123 Main Street",
            phone="1112223333",
        ),
        _build_metadata(
            3,
            name="Starbucks Coffee",
            address="123 Main St.",
            phone="111 222 3333",
        ),
        _build_metadata(
            4,
            name="Dunkin",
            address="456 Elm St",
            phone="444-555-6666",
        ),
        _build_metadata(
            5,
            name="Dunkin Donuts",
            address="456 Elm Street",
            phone="4445556666",
        ),
    ]

    # Mock the agents module to avoid import error
    mock_agents = MagicMock()
    with patch.dict("sys.modules", {"agents": mock_agents}):
        # Now we can safely import the clustering function
        from receipt_label.merchant_validation.clustering import (
            cluster_by_metadata,
        )

        # Run the clustering
        clusters = cluster_by_metadata(records)

        # Extract receipt IDs from each cluster
        result = [sorted(r.receipt_id for r in c) for c in clusters]

        # Assert that similar records are grouped together
        assert (
            len(clusters) == 2
        ), f"Expected 2 clusters but got {len(clusters)}"

        # Check that all records are accounted for
        all_ids = []
        for cluster_ids in result:
            all_ids.extend(cluster_ids)
        assert sorted(all_ids) == [
            1,
            2,
            3,
            4,
            5,
        ], "Not all records were clustered"

        # Check that Starbucks records (1, 2, 3) are in one cluster
        # and Dunkin records (4, 5) are in another
        sorted_result = sorted(result)
        assert sorted_result == [
            [1, 2, 3],
            [4, 5],
        ], f"Unexpected clustering result: {sorted_result}"
